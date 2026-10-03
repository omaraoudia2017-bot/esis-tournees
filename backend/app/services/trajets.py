"""Journée de trajets du livreur (jours sans tournée attribuée).

Le livreur saisit ses étapes de la journée (garage, contrôle technique, récupération d'une pièce…),
avec un rendez-vous facultatif et une durée sur place, choisit son point de départ et de retour
(dépôt, ou domicile s'il est livreur interne et l'a renseigné), puis fait optimiser l'ordre de
passage par le même solveur OR-Tools que les tournées : les rendez-vous sont des créneaux.

Règles :
  - uniquement les jours où AUCUNE tournée ne lui est attribuée (sinon : sa tournée fait foi) ;
  - un livreur ne voit et ne modifie que SES étapes (404 sinon, tentative tracée) ;
  - libellé, adresse et position chiffrés ; géocodage SANS le cache partagé ; le journal ne contient
    jamais l'adresse ni le libellé ;
  - le planificateur voit la journée en lecture seule (sans la position du domicile).
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import EtapeTrajet, JourneeTrajets, Tournee, Utilisateur
from app.models.enums import PrecisionGeocodage
from app.optim.cvrptw import optimiser_tournees
from app.optim.matrice import Point, construire_matrice_depart
from app.optim.probleme import JOURNEE_S, ClientTournee, ProblemeTournees, en_secondes, evaluer_parcours
from app.services.domicile import position_domicile
from app.services.geocodage import GeocoderServiceError
from app.services.journal import ContexteRequete, journaliser
from app.services.nettoyage import nettoyer_voie, normaliser_ville

POINTS = ("depot", "domicile")


class ErreurTrajets(Exception):
    def __init__(self, statut: int, message: str):
        super().__init__(message)
        self.statut, self.message = statut, message


def aujourdhui(reglages: Settings) -> date:
    return datetime.now(ZoneInfo(reglages.fuseau_horaire)).date()


def maintenant() -> datetime:
    return datetime.now(timezone.utc)


def a_une_tournee(db: Session, livreur: Utilisateur, jour: date) -> bool:
    return db.scalar(select(Tournee.id).where(Tournee.livreur_id == livreur.id,
                                              Tournee.date_tournee == jour).limit(1)) is not None


def journee_du_jour(db: Session, livreur: Utilisateur, reglages: Settings, creer: bool = False) -> JourneeTrajets | None:
    jour = aujourdhui(reglages)
    journee = db.scalar(select(JourneeTrajets).where(JourneeTrajets.livreur_id == livreur.id,
                                                     JourneeTrajets.date_journee == jour))
    if creer and a_une_tournee(db, livreur, jour):     # modification : jamais si une tournée est attribuée
        raise ErreurTrajets(409, "Une tournée vous est attribuée aujourd'hui : vos trajets sont ceux de la tournée.")
    if journee is None and creer:
        journee = JourneeTrajets(livreur_id=livreur.id, date_journee=jour)
        db.add(journee)
        db.flush()
    return journee


def etapes_de(db: Session, journee: JourneeTrajets | None) -> list[EtapeTrajet]:
    if journee is None:
        return []
    return db.scalars(select(EtapeTrajet).where(EtapeTrajet.journee_id == journee.id)
                      .order_by(EtapeTrajet.ordre.asc().nulls_last(), EtapeTrajet.id)).all()


def etape_du_livreur(db: Session, etape_id: int, livreur: Utilisateur) -> EtapeTrajet | None:
    """None si l'étape n'existe pas OU appartient à un autre livreur (même réponse 404)."""
    return db.scalar(select(EtapeTrajet).join(JourneeTrajets, JourneeTrajets.id == EtapeTrajet.journee_id)
                     .where(EtapeTrajet.id == etape_id, JourneeTrajets.livreur_id == livreur.id))


def point_etape(e: EtapeTrajet) -> Point:
    lat, lon = (float(x) for x in e.position.split(";"))
    return Point(lat, lon)


def point(nom: str, livreur: Utilisateur, reglages: Settings) -> Point:
    if nom == "domicile":
        domicile = position_domicile(livreur)
        if domicile is not None:
            return domicile
    return Point(reglages.depot_latitude, reglages.depot_longitude)


# --------------------------------------------------------------------------- saisie
@dataclass
class NouvelleEtape:
    libelle: str
    voie: str
    code_postal: str
    ville: str
    rdv_debut: time | None = None
    rdv_fin: time | None = None
    duree_min: int | None = None


def ajouter_etape(db: Session, livreur: Utilisateur, saisie: NouvelleEtape, geocodeur, reglages: Settings,
                  ctx: ContexteRequete | None) -> tuple[EtapeTrajet, str | None]:
    journee = journee_du_jour(db, livreur, reglages, creer=True)
    nb = db.scalar(select(func.count()).select_from(EtapeTrajet).where(EtapeTrajet.journee_id == journee.id))
    if nb >= reglages.trajets_max_etapes:
        raise ErreurTrajets(409, f"Au plus {reglages.trajets_max_etapes} étapes par journée.")
    voie, _ = nettoyer_voie(saisie.voie, saisie.code_postal)
    ville = normaliser_ville(saisie.ville)
    try:
        res = geocodeur.geocoder(voie, saisie.code_postal, ville)     # sans cache : donnée potentiellement personnelle
    except GeocoderServiceError:
        raise ErreurTrajets(503, "Service de localisation indisponible : réessayez plus tard.") from None
    if res.precision == PrecisionGeocodage.ECHEC or res.latitude is None:
        raise ErreurTrajets(422, "Adresse introuvable : vérifiez le numéro, la rue et le code postal.")
    etape = EtapeTrajet(journee_id=journee.id, libelle=" ".join(saisie.libelle.split()),
                        adresse=f"{voie}, {saisie.code_postal} {ville}",
                        position=f"{res.latitude:.4f};{res.longitude:.4f}",
                        position_approximative=res.precision == PrecisionGeocodage.COMMUNE,
                        rdv_debut=saisie.rdv_debut, rdv_fin=saisie.rdv_fin,
                        duree_min=saisie.duree_min or reglages.trajets_duree_defaut_min)
    db.add(etape)
    journee.optimisee_le = None                          # la liste a changé : à réoptimiser
    db.flush()
    journaliser(db, "TRAJET_ETAPE_AJOUTEE", True, ctx, livreur.id, livreur.identifiant,
                journee_id=journee.id, etape_id=etape.id)
    db.commit()
    avertissement = ("Adresse localisée seulement au centre de la commune : l'heure prévue est approximative."
                     if etape.position_approximative else None)
    return etape, avertissement


def supprimer_etape(db: Session, etape: EtapeTrajet, livreur: Utilisateur, ctx: ContexteRequete | None) -> None:
    journee = db.get(JourneeTrajets, etape.journee_id)
    etape_id = etape.id
    db.delete(etape)
    journee.optimisee_le = None
    journaliser(db, "TRAJET_ETAPE_SUPPRIMEE", True, ctx, livreur.id, livreur.identifiant,
                journee_id=journee.id, etape_id=etape_id)
    db.commit()


def marquer(db: Session, etape: EtapeTrajet, faite: bool) -> EtapeTrajet:
    if faite and etape.faite_le is not None:
        raise ErreurTrajets(409, "Étape déjà faite.")
    if not faite and etape.faite_le is None:
        raise ErreurTrajets(409, "Cette étape n'est pas marquée comme faite.")
    etape.faite_le = maintenant() if faite else None
    db.commit()
    return etape


def regler(db: Session, livreur: Utilisateur, reglages: Settings, depart: str, retour: str,
           heure_depart: time) -> JourneeTrajets:
    if "domicile" in (depart, retour) and position_domicile(livreur) is None:
        raise ErreurTrajets(422, "Le domicile n'est possible que pour un livreur interne ayant renseigné son adresse.")
    journee = journee_du_jour(db, livreur, reglages, creer=True)
    journee.depart, journee.retour, journee.heure_depart = depart, retour, heure_depart
    journee.optimisee_le = None
    db.commit()
    return journee


# --------------------------------------------------------------------------- optimisation
@dataclass
class ResultatTrajets:
    mode: str                  # "complet" ou "restant" (des étapes sont déjà faites)
    nb_etapes: int
    distance_avant_km: float   # ordre de saisie
    distance_apres_km: float
    nb_retards: int            # rendez-vous qui risquent d'être manqués
    duree_calcul_s: float


def _horodatage(jour: date, secondes: int, reglages: Settings) -> datetime:
    return datetime.combine(jour, time(0), tzinfo=ZoneInfo(reglages.fuseau_horaire)) + timedelta(seconds=int(secondes))


def optimiser(db: Session, livreur: Utilisateur, reglages: Settings, ctx: ContexteRequete | None) -> ResultatTrajets:
    journee = journee_du_jour(db, livreur, reglages)
    etapes = etapes_de(db, journee)
    faites = sorted([e for e in etapes if e.faite_le], key=lambda e: e.faite_le)
    restantes = [e for e in etapes if not e.faite_le]
    if not restantes:
        raise ErreurTrajets(409, "Aucune étape restante à optimiser.")

    fuseau = ZoneInfo(reglages.fuseau_horaire)
    depart_s, mode = en_secondes(journee.heure_depart), "complet"
    depart = point(journee.depart, livreur, reglages)
    if faites:                                            # en cours de journée : depuis la dernière étape faite
        mode = "restant"
        depart = point_etape(faites[-1])
        local = maintenant().astimezone(fuseau)
        depart_s = max(depart_s, local.hour * 3600 + local.minute * 60)
    fin_s = min(max(en_secondes(reglages.heure_fin_tournee), depart_s + 3600), JOURNEE_S - 1)

    clients = [ClientTournee(identifiant=str(e.id),
                             debut_s=en_secondes(e.rdv_debut) if e.rdv_debut else 0,
                             fin_s=en_secondes(e.rdv_fin) if e.rdv_fin else JOURNEE_S - 1,
                             temps_service_s=e.duree_min * 60) for e in restantes]
    depot = Point(reglages.depot_latitude, reglages.depot_longitude)
    matrice = construire_matrice_depart(depart, depot, [point_etape(e) for e in restantes],
                                        reglages.coefficient_routier, reglages.vitesse_moyenne_kmh,
                                        arrivee=point(journee.retour, livreur, reglages))
    probleme = ProblemeTournees(clients=clients, matrice=matrice, nb_vehicules=1, max_clients=len(clients),
                                heure_depart_s=depart_s, heure_fin_s=fin_s,
                                penalite_retard_par_seconde=reglages.penalite_retard_par_seconde,
                                penalite_client_non_servi=reglages.penalite_client_non_servi)
    res = optimiser_tournees(probleme, reglages.optim_temps_limite_s)
    ordre, evaluation = res.tournees[0], res.evaluations[0]
    avant = evaluer_parcours(probleme, list(range(1, len(clients) + 1)))        # ordre de saisie

    for rang, e in enumerate(faites, start=1):
        e.ordre = rang
    for rang, (noeud, passage) in enumerate(zip(ordre, evaluation.passages), start=len(faites) + 1):
        e = restantes[noeud - 1]
        e.ordre = rang
        e.heure_prevue = _horodatage(journee.date_journee, passage.arrivee_s, reglages)
    for rang, noeud in enumerate(res.non_servis, start=len(faites) + len(ordre) + 1):
        restantes[noeud - 1].ordre, restantes[noeud - 1].heure_prevue = rang, None
    journee.optimisee_le = maintenant()
    journee.distance_km = round(evaluation.distance_m / 1000, 2)
    # durée depuis l'heure de départ de la journée : heure de retour prévue = heure_depart + duree_min
    journee.duree_min = max(0, (evaluation.retour_depot_s - en_secondes(journee.heure_depart)) // 60)
    journaliser(db, "TRAJETS_OPTIMISES", True, ctx, livreur.id, livreur.identifiant,
                journee_id=journee.id, nb_etapes=len(restantes))
    db.commit()
    return ResultatTrajets(mode=mode, nb_etapes=len(restantes),
                           distance_avant_km=round(avant.distance_m / 1000, 1),
                           distance_apres_km=round(evaluation.distance_m / 1000, 1),
                           nb_retards=evaluation.nb_retards, duree_calcul_s=res.duree_calcul_s)


# --------------------------------------------------------------------------- planificateur
def journees_du(db: Session, jour: date) -> list[tuple[JourneeTrajets, Utilisateur, list[EtapeTrajet]]]:
    journees = db.execute(select(JourneeTrajets, Utilisateur)
                          .join(Utilisateur, Utilisateur.id == JourneeTrajets.livreur_id)
                          .where(JourneeTrajets.date_journee == jour)
                          .order_by(Utilisateur.nom, Utilisateur.prenom)).all()
    return [(j, u, etapes_de(db, j)) for j, u in journees]
