"""Tournée du jour d'un livreur : consultation, optimisation en un clic, pointage terrain.

Règles (cahier des charges v6, module 2) :
  - un livreur ne voit et ne modifie que SA tournée (sinon : 404 + trace dans le journal) ;
  - « Optimiser » réordonne les clients avec le solveur CVRPTW (créneaux, temps de service,
    type de véhicule, limite d'effectif) et enregistre l'ordre et les heures de passage prévues ;
  - « Démarrer » puis « Terminer » horodatent la livraison ; PostgreSQL calcule le temps de
    service réel (heure_fin - heure_debut), donnée d'entraînement du futur modèle prédictif ;
  - (module 3) une adresse peut être ajoutée en cours de tournée, sauf si le véhicule est
    inadapté ou si la limite d'effectif de la zone est atteinte ; « Optimiser » recalcule alors
    uniquement l'itinéraire restant, depuis la position actuelle du livreur.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Client, Livraison, Tournee, Utilisateur, Vehicule, Zone, ZoneCodePostal
from app.models.enums import (PrecisionGeocodage, SourceClient, StatutLivraison, StatutTournee,
                              TypeCamion, TypeClient)
from app.optim.cvrptw import optimiser_tournees
from app.optim.matrice import Point, construire_matrice, longueur_parcours
from app.optim.probleme import JOURNEE_S, en_secondes, evaluer_parcours
from app.services.geocodage import GeocoderServiceError, geocoder_avec_cache
from app.services.journal import ContexteRequete, journaliser
from app.services.nettoyage import (est_professionnel, extraire_acces, nettoyer_digicode,
                                    nettoyer_voie, normaliser_ville)
from app.services.planification import construire_probleme, estimer_temps_service_s
from app.services.sectorisation import zone_pour

EN_ATTENTE = (StatutLivraison.A_LIVRER, StatutLivraison.EN_COURS)
ISSUES_POSSIBLES = (StatutLivraison.LIVRE, StatutLivraison.ABSENT, StatutLivraison.REFUSE)
PREFIXE_SAISIE = "7"            # identifiants des clients saisis en cours de tournée : 7xxxxxxxxx


class ErreurTournee(Exception):
    def __init__(self, statut: int, message: str):
        super().__init__(message)
        self.statut, self.message = statut, message


def aujourdhui(reglages: Settings) -> date:
    return datetime.now(ZoneInfo(reglages.fuseau_horaire)).date()


def maintenant() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- lecture
def tournee_du_jour(db: Session, livreur: Utilisateur, reglages: Settings) -> Tournee:
    tournee = db.scalar(select(Tournee).where(Tournee.livreur_id == livreur.id,
                                              Tournee.date_tournee == aujourdhui(reglages)))
    if tournee is None:
        raise ErreurTournee(404, "Aucune tournée ne vous est attribuée aujourd'hui.")
    return tournee


def livraisons_de(db: Session, tournee: Tournee) -> list[tuple[Livraison, Client]]:
    """Ordre de passage optimisé s'il existe, sinon ordre brut d'attribution."""
    return db.execute(
        select(Livraison, Client).join(Client, Client.id_client == Livraison.client_id)
        .where(Livraison.tournee_id == tournee.id)
        .order_by(Livraison.ordre_passage.asc().nulls_last(), Livraison.id)
    ).all()


def livraison_du_livreur(db: Session, livraison_id: int, livreur: Utilisateur) -> Livraison:
    """Renvoie None si la livraison n'existe pas OU appartient à un autre livreur : l'appelant
    répond 404 dans les deux cas (on ne révèle pas l'existence des tournées des collègues)."""
    return db.scalar(select(Livraison).join(Tournee, Tournee.id == Livraison.tournee_id)
                     .where(Livraison.id == livraison_id, Tournee.livreur_id == livreur.id))


# --------------------------------------------------------------------------- optimisation
@dataclass
class ResultatOptimisation:
    mode: str                          # "complet" (avant départ) ou "restant" (en cours de tournée)
    nb_clients: int
    nb_optimises: int
    distance_avant_km: float           # mêmes clients, ordre actuel de la liste
    distance_apres_km: float
    nb_retards: int
    duree_calcul_s: float
    a_reporter: list[str] = field(default_factory=list)
    temps_service: str = "fixe"        # "modele" : temps sur place prédits par le Random Forest


def bornes_horaires(tournee: Tournee, reglages: Settings) -> tuple[int, int]:
    depart = tournee.heure_depart or reglages.heure_debut_tournee
    fin = tournee.heure_fin or reglages.heure_fin_tournee
    return en_secondes(depart), en_secondes(fin)


def _horodatage(jour: date, secondes: int, reglages: Settings) -> datetime:
    minuit = datetime.combine(jour, time(0), tzinfo=ZoneInfo(reglages.fuseau_horaire))
    return minuit + timedelta(seconds=int(secondes))


def _secondes_locales(instant: datetime, reglages: Settings) -> int:
    local = instant.astimezone(ZoneInfo(reglages.fuseau_horaire))
    return local.hour * 3600 + local.minute * 60 + local.second


def _point(c: Client) -> Point | None:
    return None if c.latitude is None or c.longitude is None else Point(float(c.latitude),
                                                                        float(c.longitude))


def _km_parcourus(faites: list[tuple[Livraison, Client]], depot: Point, reglages: Settings) -> float:
    """Distance estimée déjà parcourue : dépôt -> clients terminés, dans l'ordre réel de passage."""
    points = [depot] + [p for _, c in faites if (p := _point(c)) is not None]
    if len(points) < 2:
        return 0.0
    m = construire_matrice(points, reglages.coefficient_routier, reglages.vitesse_moyenne_kmh)
    return longueur_parcours(m.distances_m, list(range(1, len(points))), retour_depot=False) / 1000


def optimiser_tournee(db: Session, tournee: Tournee, reglages: Settings) -> ResultatOptimisation:
    """Avant le départ : optimise toute la tournée depuis le dépôt.
    En cours de tournée : les livraisons terminées sont figées ; seules les livraisons restantes
    sont réordonnées, depuis la position du dernier client servi et à partir de l'heure actuelle."""
    if tournee.statut == StatutTournee.TERMINEE:
        raise ErreurTournee(409, "La tournée est terminée.")
    lignes = livraisons_de(db, tournee)
    if any(l.statut == StatutLivraison.EN_COURS for l, _ in lignes):
        raise ErreurTournee(409, "Terminez la livraison en cours avant de recalculer l'itinéraire.")

    # Un client « reporté » lors d'un calcul précédent est remis dans le calcul
    for livraison, _ in lignes:
        if livraison.statut == StatutLivraison.REPORTE:
            livraison.statut = StatutLivraison.A_LIVRER
    faites = sorted([(l, c) for l, c in lignes if l.statut in ISSUES_POSSIBLES],
                    key=lambda lc: lc[0].heure_debut_livraison)
    restantes = [(l, c) for l, c in lignes if l.statut == StatutLivraison.A_LIVRER]
    if not restantes:
        raise ErreurTournee(409, "Aucune livraison restante à optimiser.")

    depot = Point(reglages.depot_latitude, reglages.depot_longitude)
    depart_tournee_s, fin_s = bornes_horaires(tournee, reglages)
    depart_s, position, mode = depart_tournee_s, None, "complet"
    if tournee.statut == StatutTournee.EN_COURS:
        mode = "restant"
        position = next((p for _, c in reversed(faites) if (p := _point(c)) is not None), depot)
        depart_s = max(depart_tournee_s, _secondes_locales(maintenant(), reglages))
        # En cours de journée, le retour peut glisser jusqu'à la fin de journée (retards pénalisés)
        fin_s = min(max(fin_s, en_secondes(reglages.heure_fin_tournee), depart_s + 3600), JOURNEE_S - 1)

    localises = [(l, c) for l, c in restantes if _point(c) is not None]
    non_localises = [(l, c) for l, c in restantes if _point(c) is None]
    vehicule = db.get(Vehicule, tournee.vehicule_id) if tournee.vehicule_id else None
    clients = [c for _, c in localises]
    temps_s, source_temps = estimer_temps_service_s(clients, reglages)
    probleme = construire_probleme(
        clients, reglages, tournee.max_clients, nb_vehicules=1,
        depart_s=depart_s, fin_s=fin_s, position=position, temps_service_s=temps_s,
        # Segmentation de flotte : un client hayon ne peut pas être servi par un camion normal
        types_vehicules=[vehicule.type_camion.value] if vehicule else None,
    )
    res = optimiser_tournees(probleme, reglages.optim_temps_limite_s)
    ordre, evaluation = res.tournees[0], res.evaluations[0]
    avant = evaluer_parcours(probleme, sorted(ordre))     # mêmes clients, ordre actuel de la liste

    # Numérotation : livraisons terminées, puis itinéraire optimisé, puis clients à reporter
    for rang, (livraison, _) in enumerate(faites, start=1):
        livraison.ordre_passage = rang
    for rang, (noeud, passage) in enumerate(zip(ordre, evaluation.passages), start=len(faites) + 1):
        livraison, client = localises[noeud - 1]
        livraison.ordre_passage = rang
        livraison.heure_arrivee_prevue = _horodatage(tournee.date_tournee, passage.arrivee_s, reglages)
        livraison.temps_service_predit_s = probleme.clients[noeud - 1].temps_service_s
    reportes = [localises[k - 1] for k in res.non_servis] + non_localises
    for rang, (livraison, _) in enumerate(reportes, start=len(faites) + len(ordre) + 1):
        livraison.ordre_passage = rang
        livraison.heure_arrivee_prevue = None
        livraison.statut = StatutLivraison.REPORTE

    tournee.optimisee_le = maintenant()
    tournee.distance_totale_km = round(_km_parcourus(faites, depot, reglages)
                                       + evaluation.distance_m / 1000, 2)
    tournee.duree_estimee_min = max(0, (evaluation.retour_depot_s - depart_tournee_s) // 60)
    db.commit()

    return ResultatOptimisation(
        mode=mode,
        nb_clients=len(lignes),
        nb_optimises=len(ordre),
        distance_avant_km=round(avant.distance_m / 1000, 1),
        distance_apres_km=round(evaluation.distance_m / 1000, 1),
        nb_retards=evaluation.nb_retards,
        duree_calcul_s=res.duree_calcul_s,
        a_reporter=[c.id_client for _, c in reportes],
        temps_service=source_temps,
    )


# --------------------------------------------------------------------------- ajout à la volée
@dataclass
class NouveauClient:
    nom: str
    voie: str
    code_postal: str
    ville: str
    type_camion_requis: str = "normal"
    creneau_debut: time | None = None
    creneau_fin: time | None = None
    telephone: str | None = None
    digicode: str | None = None
    notes_livraison: str | None = None


def _nouvel_identifiant(db: Session) -> str:
    dernier = db.scalar(select(func.max(Client.id_client))
                        .where(Client.id_client.like(f"{PREFIXE_SAISIE}%")))
    return f"{PREFIXE_SAISIE}{(int(dernier[1:]) if dernier else 0) + 1:09d}"


def _zone_id(db: Session, code_postal: str, latitude: float, longitude: float) -> int | None:
    rattachement = db.get(ZoneCodePostal, code_postal)
    if rattachement is not None:
        return rattachement.zone_id
    return db.scalar(select(Zone.id).where(Zone.nom == zone_pour(code_postal, latitude, longitude).nom))


def ajouter_client(db: Session, tournee: Tournee, saisie: NouveauClient, geocodeur,
                   reglages: Settings, livreur: Utilisateur,
                   ctx: ContexteRequete | None = None) -> tuple[Livraison, Client, str | None]:
    """Ajoute une nouvelle adresse à la tournée en cours. Les règles de refus sont vérifiées
    AVANT le géocodage (aucun appel inutile à Nominatim) ; chaque refus est journalisé."""

    def refuser(motif: str, message: str, statut: int = 409):
        journaliser(db, "AJOUT_CLIENT_REFUSE", False, ctx, livreur.id, livreur.identifiant,
                    tournee_id=tournee.id, motif=motif)
        db.commit()
        raise ErreurTournee(statut, message)

    if tournee.statut == StatutTournee.TERMINEE:
        raise ErreurTournee(409, "La tournée est terminée : impossible d'ajouter un client.")

    # Règle 1 : véhicule compatible (segmentation de flotte)
    type_requis = TypeCamion(saisie.type_camion_requis)
    vehicule = db.get(Vehicule, tournee.vehicule_id) if tournee.vehicule_id else None
    if vehicule is not None and vehicule.type_camion != type_requis:
        refuser("vehicule_incompatible",
                f"Ajout refusé : ce client nécessite un camion « {type_requis.value} » et votre "
                f"véhicule {vehicule.immatriculation} est de type « {vehicule.type_camion.value} ». "
                "Reportez ce client sur une tournée compatible.")

    # Règle 2 : limite d'effectif de la zone (également garantie par un trigger PostgreSQL)
    nb = db.scalar(select(func.count()).select_from(Livraison).where(Livraison.tournee_id == tournee.id))
    if nb >= tournee.max_clients:
        refuser("limite_effectif",
                f"Ajout refusé : la tournée compte déjà {nb} clients, limite de la zone "
                f"({tournee.max_clients}) atteinte. Reportez ce client sur une tournée ultérieure.")

    # Nettoyage (mêmes règles que l'import CSV) puis géocodage avec cache
    code_postal = saisie.code_postal.strip()
    voie, complement = nettoyer_voie(saisie.voie, code_postal)
    ville = normaliser_ville(saisie.ville)
    if not voie or not ville:
        raise ErreurTournee(422, "Adresse incomplète : indiquez le numéro et la rue, puis la ville.")
    try:
        geo = geocoder_avec_cache(db, geocodeur, voie, code_postal, ville)
    except GeocoderServiceError:
        raise ErreurTournee(503, "Service de géocodage indisponible : réessayez dans un instant.") from None
    if geo.latitude is None or geo.precision == PrecisionGeocodage.ECHEC:
        refuser("adresse_introuvable",
                "Adresse introuvable : vérifiez le numéro, la rue, le code postal et la ville.", 422)

    nom = " ".join(saisie.nom.upper().split())[:100]
    notes = " ".join((saisie.notes_livraison or "").split()) or None
    if complement:
        notes = f"Complement d adresse : {complement}. {notes or ''}".strip()
    immeuble, etage, ascenseur = extraire_acces(notes)
    client = Client(
        id_client=_nouvel_identifiant(db), nom_client=nom,
        type_client=TypeClient.PROFESSIONNEL if est_professionnel(nom, notes) else TypeClient.PARTICULIER,
        adresse=f"{voie}, {code_postal} {ville}", voie=voie, code_postal=code_postal, ville=ville,
        latitude=Decimal(str(round(geo.latitude, 6))), longitude=Decimal(str(round(geo.longitude, 6))),
        geocode_precision=geo.precision, geocode_le=maintenant(),
        telephone=(saisie.telephone or "").strip() or None,
        digicode=nettoyer_digicode(saisie.digicode), notes_livraison=notes,
        creneau_debut=saisie.creneau_debut, creneau_fin=saisie.creneau_fin,
        type_camion_requis=type_requis,
        est_immeuble=immeuble, etage=etage if etage is None or -5 <= etage <= 60 else None,
        ascenseur=ascenseur,
        zone_id=_zone_id(db, code_postal, geo.latitude, geo.longitude),
        source=SourceClient.SAISIE_MANUELLE,
    )
    livraison = Livraison(tournee_id=tournee.id, client_id=client.id_client, ajout_a_la_volee=True,
                          temps_service_predit_s=estimer_temps_service_s([client], reglages)[0][0])
    db.add(client)
    db.flush()
    db.add(livraison)
    try:
        db.flush()
    except IntegrityError:          # trigger de limite : deux ajouts simultanés
        db.rollback()
        refuser("limite_effectif", f"Ajout refusé : limite de {tournee.max_clients} clients atteinte.")

    journaliser(db, "AJOUT_CLIENT_VOLEE", True, ctx, livreur.id, livreur.identifiant,
                tournee_id=tournee.id, client_id=client.id_client)
    db.commit()
    avertissement = None
    if geo.precision == PrecisionGeocodage.COMMUNE:
        avertissement = ("Adresse localisée seulement au centre de la commune : "
                         "l'heure de passage prévue est approximative.")
    return livraison, client, avertissement


# --------------------------------------------------------------------------- pointage terrain
def _tournee_modifiable(db: Session, livraison: Livraison, reglages: Settings) -> Tournee:
    tournee = db.get(Tournee, livraison.tournee_id)
    if tournee.date_tournee != aujourdhui(reglages):
        raise ErreurTournee(409, "Cette livraison n'appartient pas à la tournée du jour.")
    return tournee


def demarrer_livraison(db: Session, livraison: Livraison, reglages: Settings) -> Livraison:
    tournee = _tournee_modifiable(db, livraison, reglages)
    if livraison.statut != StatutLivraison.A_LIVRER:
        raise ErreurTournee(409, "Cette livraison ne peut pas être démarrée (déjà traitée ou reportée).")
    en_cours = db.scalar(select(Livraison.id).where(Livraison.tournee_id == tournee.id,
                                                   Livraison.statut == StatutLivraison.EN_COURS))
    if en_cours is not None:
        raise ErreurTournee(409, "Terminez d'abord la livraison en cours.")

    livraison.statut = StatutLivraison.EN_COURS
    livraison.heure_debut_livraison = maintenant()
    tournee.statut = StatutTournee.EN_COURS
    db.commit()
    return livraison


def terminer_livraison(db: Session, livraison: Livraison, issue: StatutLivraison,
                       reglages: Settings) -> Livraison:
    tournee = _tournee_modifiable(db, livraison, reglages)
    if issue not in ISSUES_POSSIBLES:
        raise ErreurTournee(422, "Issue invalide (livre, absent ou refuse).")
    if livraison.statut != StatutLivraison.EN_COURS:
        raise ErreurTournee(409, "Cette livraison n'a pas été démarrée.")

    livraison.statut = issue
    livraison.heure_fin_livraison = maintenant()
    if issue == StatutLivraison.LIVRE:
        # RGPD : date de dernière livraison, base du calcul de la durée de conservation
        db.get(Client, livraison.client_id).derniere_livraison_le = livraison.heure_fin_livraison
    db.flush()

    restantes = db.scalar(select(Livraison.id).where(Livraison.tournee_id == tournee.id,
                                                    Livraison.statut.in_(EN_ATTENTE)).limit(1))
    if restantes is None:
        tournee.statut = StatutTournee.TERMINEE
    db.commit()
    db.refresh(livraison)          # relit temps_service_reel_s, calculé par PostgreSQL
    return livraison
