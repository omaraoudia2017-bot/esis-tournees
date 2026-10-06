"""Espace administrateur (cahier des charges v6, module 4) : logique métier.

  - comptes : création, désactivation / réactivation, déverrouillage, réinitialisation du mot de
    passe et de la 2FA, affectation du véhicule ;
  - tournées du jour : attribution d'une liste de clients à un livreur, ajout d'un client existant ;
  - clients : recherche, correction de position (géocodage approximatif) et du type de camion,
    droits RGPD (export, anonymisation) ;
  - tableau de bord : respect des créneaux, remplissage des tournées, écart des temps de service.

Chaque action est inscrite au journal de sécurité avec l'administrateur qui l'a faite.
Les routes (app/api/routes_admin.py) ne font que traduire les ErreurAdmin en réponses HTTP.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from statistics import mean
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import generer_code_activation, hacher_mdp, normaliser_code_activation
from app.models import (CacheGeocodage, Client, CodeSecours, JournalSecurite, Livraison, Tournee,
                        Utilisateur, Vehicule, Zone)
from app.models.enums import (PrecisionGeocodage, RoleUtilisateur, SourceClient, StatutLivraison,
                              StatutTournee, TypeCamion, TypeLivreur)
from app.optim.faisabilite import limite_periode
from app.optim.probleme import en_heure, en_secondes
from app.optim.segmentation import Periode
from app.services.domicile import effacer_domicile
from app.services.geocodage import cle_cache
from app.services.journal import ContexteRequete, journaliser
from app.services.planification import estimer_temps_service_s, faisabilite, periodes
from app.services.referentiel import zone_id_pour
from app.services.remarques import effacer_remarques_client, toutes_les_remarques
from app.services.tournees import ISSUES_POSSIBLES, aujourdhui, maintenant

DUREE_PLAUSIBLE_S = (60, 90 * 60)        # pointages retenus pour l'écart de temps de service
FRANCE = ((41.0, 51.5), (-5.5, 10.0))    # bornes des corrections de position


class ErreurAdmin(Exception):
    def __init__(self, statut: int, message: str):
        super().__init__(message)
        self.statut, self.message = statut, message


@dataclass
class Acteur:
    """Administrateur à l'origine de l'action (pour le journal)."""
    utilisateur: Utilisateur
    ctx: ContexteRequete | None = None

    def tracer(self, db: Session, evenement: str, succes: bool = True, **details) -> None:
        journaliser(db, evenement, succes, self.ctx, self.utilisateur.id,
                    self.utilisateur.identifiant, **details)


def _heure(secondes: int) -> time:
    return time(min(secondes // 3600, 23), secondes % 3600 // 60)


# =========================================================================== comptes
def lister_comptes(db: Session) -> list[tuple[Utilisateur, Vehicule | None]]:
    return db.execute(select(Utilisateur, Vehicule)
                      .outerjoin(Vehicule, Vehicule.id == Utilisateur.vehicule_id)
                      .order_by(Utilisateur.role, Utilisateur.identifiant)).all()


def lister_vehicules(db: Session) -> list[tuple[Vehicule, str | None]]:
    """Véhicules avec, le cas échéant, le livreur actif qui l'utilise."""
    affectes = {v: i for v, i in db.execute(
        select(Utilisateur.vehicule_id, Utilisateur.identifiant)
        .where(Utilisateur.actif.is_(True), Utilisateur.vehicule_id.is_not(None)))}
    return [(v, affectes.get(v.id)) for v in db.scalars(select(Vehicule).order_by(Vehicule.immatriculation))]


def _vehicule_valide(db: Session, vehicule_id: int | None) -> Vehicule | None:
    if vehicule_id is None:
        return None
    vehicule = db.get(Vehicule, vehicule_id)
    if vehicule is None or not vehicule.actif:
        raise ErreurAdmin(422, "Véhicule inconnu ou hors service.")
    return vehicule


def creer_compte(db: Session, acteur: Acteur, identifiant: str, nom: str, prenom: str,
                 role: RoleUtilisateur, vehicule_id: int | None,
                 type_livreur: str | None = None) -> tuple[Utilisateur, str]:
    """Retourne (compte, code d'activation). Le code n'est affiché qu'une fois au planificateur, qui
    le remet au salarié ; à la première connexion, celui-ci choisit son mot de passe puis active la
    2FA (QR code). Personne d'autre que lui ne connaît son mot de passe."""
    identifiant = identifiant.strip().lower()
    if db.scalar(select(Utilisateur.id).where(func.lower(Utilisateur.identifiant) == identifiant)):
        raise ErreurAdmin(409, "Cet identifiant existe déjà.")
    if role == RoleUtilisateur.ADMIN and vehicule_id is not None:
        raise ErreurAdmin(422, "Un administrateur n'a pas de véhicule.")
    vehicule = _vehicule_valide(db, vehicule_id)
    u = Utilisateur(identifiant=identifiant, nom=" ".join(nom.upper().split()),
                    prenom=" ".join(prenom.split()), role=role,
                    vehicule_id=vehicule.id if vehicule else None,
                    type_livreur=TypeLivreur(type_livreur or "prestataire")
                    if role == RoleUtilisateur.LIVREUR else None)
    code = nouveau_code_activation(u)
    db.add(u)
    db.flush()
    acteur.tracer(db, "ADMIN_CREATION_COMPTE", cible=identifiant, role=role.value,
                  type_livreur=u.type_livreur.value if u.type_livreur else None)
    db.commit()
    return u, code


def nouveau_code_activation(u: Utilisateur) -> str:
    """Remplace le mot de passe par un code d'activation à usage unique (haché comme un mot de
    passe, jamais journalisé), valable `activation_validite_heures`."""
    code = generer_code_activation()
    u.mot_de_passe_hash = hacher_mdp(normaliser_code_activation(code))
    u.mdp_a_definir = True
    u.activation_expire_le = maintenant() + timedelta(hours=get_settings().activation_validite_heures)
    return code


ACTIONS_COMPTE = ("desactiver", "reactiver", "deverrouiller", "reinitialiser-mdp", "reinitialiser-2fa")


def agir_sur_compte(db: Session, acteur: Acteur, utilisateur_id: int, action: str) -> tuple[Utilisateur, str | None]:
    """Retourne (compte, code d'activation si le mot de passe est réinitialisé)."""
    u = db.get(Utilisateur, utilisateur_id)
    if u is None:
        raise ErreurAdmin(404, "Compte introuvable.")
    if u.id == acteur.utilisateur.id and action in ("desactiver", "reinitialiser-mdp", "reinitialiser-2fa"):
        # évite qu'un administrateur se ferme lui-même l'accès par erreur
        raise ErreurAdmin(409, "Action impossible sur votre propre compte.")
    mdp = None
    if action == "desactiver":
        u.actif = False
        u.sessions_invalidees_le = maintenant()          # effet immédiat sur les sessions ouvertes
    elif action == "reactiver":
        u.actif = True
    elif action == "deverrouiller":
        u.verrouille_jusqua, u.tentatives_echec = None, 0
    elif action == "reinitialiser-mdp":
        # Mot de passe oublié : nouveau code ; la 2FA déjà activée est conservée
        mdp = nouveau_code_activation(u)
        u.verrouille_jusqua, u.tentatives_echec = None, 0
        u.sessions_invalidees_le = maintenant()
    elif action == "reinitialiser-2fa":                  # téléphone perdu : nouvel enrôlement
        u.totp_secret, u.totp_active, u.totp_dernier_pas = None, False, None
        db.execute(delete(CodeSecours).where(CodeSecours.utilisateur_id == u.id))
        u.sessions_invalidees_le = maintenant()
    else:
        raise ErreurAdmin(422, "Action inconnue.")
    acteur.tracer(db, f"ADMIN_{action.upper().replace('-', '_')}", cible=u.identifiant)
    db.commit()
    return u, mdp


def changer_type_livreur(db: Session, acteur: Acteur, utilisateur_id: int, type_livreur: str) -> Utilisateur:
    u = db.get(Utilisateur, utilisateur_id)
    if u is None:
        raise ErreurAdmin(404, "Compte introuvable.")
    if u.role != RoleUtilisateur.LIVREUR:
        raise ErreurAdmin(422, "Seul un livreur est interne ou prestataire.")
    u.type_livreur = TypeLivreur(type_livreur)
    # Minimisation : un prestataire n'a pas besoin d'adresse de domicile dans l'application
    efface = effacer_domicile(u) if u.type_livreur == TypeLivreur.PRESTATAIRE else False
    acteur.tracer(db, "ADMIN_TYPE_LIVREUR", cible=u.identifiant, type_livreur=type_livreur,
                  domicile_efface=efface)
    db.commit()
    return u


def affecter_vehicule(db: Session, acteur: Acteur, utilisateur_id: int,
                      vehicule_id: int | None) -> Utilisateur:
    u = db.get(Utilisateur, utilisateur_id)
    if u is None:
        raise ErreurAdmin(404, "Compte introuvable.")
    if u.role != RoleUtilisateur.LIVREUR:
        raise ErreurAdmin(422, "Seul un livreur reçoit un véhicule.")
    vehicule = _vehicule_valide(db, vehicule_id)
    u.vehicule_id = vehicule.id if vehicule else None
    acteur.tracer(db, "ADMIN_AFFECTATION_VEHICULE", cible=u.identifiant,
                  vehicule=vehicule.immatriculation if vehicule else None)
    db.commit()
    return u


# =========================================================================== tournées
def lister_tournees(db: Session, jour: date | None, en_attente_depuis: date | None = None):
    """Tournées d'un jour ; ou, avec `en_attente_depuis`, toutes les tournées sans livreur à venir."""
    filtre = ([Tournee.date_tournee == jour] if en_attente_depuis is None
              else [Tournee.livreur_id.is_(None), Tournee.date_tournee >= en_attente_depuis])
    nb = (select(Livraison.tournee_id, func.count().label("nb"),
                 func.count().filter(Livraison.statut.in_(ISSUES_POSSIBLES)).label("faites"),
                 func.count().filter(Livraison.statut == StatutLivraison.REPORTE).label("reportes"))
          .group_by(Livraison.tournee_id).subquery())
    return db.execute(
        select(Tournee, Utilisateur, Vehicule, Zone, nb.c.nb, nb.c.faites, nb.c.reportes)
        .outerjoin(Utilisateur, Utilisateur.id == Tournee.livreur_id)        # en attente : sans livreur
        .outerjoin(Vehicule, Vehicule.id == Tournee.vehicule_id)
        .outerjoin(Zone, Zone.id == Tournee.zone_id)
        .outerjoin(nb, nb.c.tournee_id == Tournee.id)
        .where(*filtre).order_by(Tournee.date_tournee, Utilisateur.identifiant.nulls_first())).all()


def _periode(nom: str, reglages: Settings) -> Periode:
    toutes = periodes(reglages) + periodes(reglages, journee_continue=True)
    for p in toutes:
        if p.nom == nom:
            return p
    raise ErreurAdmin(422, "Période inconnue (matin, apres-midi, soiree ou journee).")


def _deja_planifies(db: Session, jour: date, ids: list[str], sauf_tournee: int | None = None) -> list[str]:
    requete = (select(Livraison.client_id).join(Tournee, Tournee.id == Livraison.tournee_id)
               .where(Tournee.date_tournee == jour, Livraison.client_id.in_(ids)))
    if sauf_tournee is not None:
        requete = requete.where(Tournee.id != sauf_tournee)
    return sorted(set(db.scalars(requete)))


def _controler_clients(db: Session, ids: list[str], vehicule: Vehicule) -> list[Client]:
    clients = {c.id_client: c for c in db.scalars(select(Client).where(Client.id_client.in_(ids)))}
    inconnus = [i for i in ids if i not in clients or not clients[i].actif]
    if inconnus:
        raise ErreurAdmin(422, "Clients inconnus ou anonymisés : " + ", ".join(inconnus))
    non_localises = [i for i in ids if clients[i].latitude is None]
    if non_localises:
        raise ErreurAdmin(422, "Clients sans position (géocodage à faire ou à corriger) : "
                          + ", ".join(non_localises))
    # Segmentation de flotte (cahier des charges) : un client hayon exige un camion hayon
    incompatibles = [i for i in ids if clients[i].type_camion_requis != vehicule.type_camion]
    if incompatibles:
        raise ErreurAdmin(409, f"Camion {vehicule.immatriculation} ({vehicule.type_camion.value}) "
                          "incompatible avec : " + ", ".join(incompatibles)
                          + ". Attribuez ces clients à un livreur équipé du bon camion.")
    return [clients[i] for i in ids]


def creer_tournee(db: Session, acteur: Acteur, reglages: Settings, livreur_id: int,
                  ids_clients: list[str], jour: date | None = None, periode: str = "matin",
                  remplacer: bool = False) -> Tournee:
    """Tournée du jour d'un livreur, dans l'ordre fourni (« liste brute ») : le livreur
    l'optimise ensuite avec son bouton. Refus si véhicule inadapté ou limite d'effectif dépassée."""
    jour = jour or aujourdhui(reglages)
    if jour < aujourdhui(reglages):
        raise ErreurAdmin(422, "Impossible de planifier une tournée dans le passé.")
    ids = list(dict.fromkeys(i.strip() for i in ids_clients if i.strip()))
    if not ids:
        raise ErreurAdmin(422, "Aucun client sélectionné.")
    livreur = db.get(Utilisateur, livreur_id)
    if livreur is None or livreur.role != RoleUtilisateur.LIVREUR or not livreur.actif:
        raise ErreurAdmin(404, "Livreur introuvable ou désactivé.")
    vehicule = db.get(Vehicule, livreur.vehicule_id) if livreur.vehicule_id else None
    if vehicule is None:
        raise ErreurAdmin(409, f"{livreur.identifiant} n'a pas de véhicule : affectez-lui un camion.")
    p = _periode(periode, reglages)

    existante = db.scalar(select(Tournee).where(Tournee.livreur_id == livreur.id,
                                                Tournee.date_tournee == jour))
    if existante is not None:
        if not remplacer:
            raise ErreurAdmin(409, f"{livreur.identifiant} a déjà une tournée le {jour:%d/%m/%Y}.")
        if existante.statut != StatutTournee.PLANIFIEE:
            raise ErreurAdmin(409, "La tournée existante a commencé : elle ne peut plus être remplacée.")
    clients = _controler_clients(db, ids, vehicule)
    doublons = _deja_planifies(db, jour, ids, existante.id if existante else None)
    if doublons:
        raise ErreurAdmin(409, "Clients déjà dans une autre tournée ce jour-là : " + ", ".join(doublons))

    # Limite d'effectif : celle de la zone majoritaire (10 à 20 clients selon la densité), réduite
    # pour une demi-journée (N clients par heure de la période) : 20 clients = journée complète
    zone_id = Counter(c.zone_id for c in clients).most_common(1)[0][0]
    zone = db.get(Zone, zone_id) if zone_id else None
    limite_zone = zone.max_clients if zone else 15
    limite = limite_periode(p, limite_zone, reglages.clients_max_par_heure)
    if len(ids) > limite:
        precision = "" if limite == limite_zone else (
            f", période {p.nom} : {reglages.clients_max_par_heure} clients par heure au plus")
        raise ErreurAdmin(409, f"Tournée non attribuée : {len(ids)} clients pour une limite de {limite} "
                          f"(zone {zone.nom if zone else 'non définie'}{precision}). Retirez {len(ids) - limite} "
                          f"client(s) de la liste : ils pourront être attribués à un autre livreur ou un autre jour.")
    # Ordre initial = ordre horaire des créneaux ; le livreur optimise ensuite l'ordre géographique
    clients = sorted(clients, key=lambda c: (c.creneau_debut or time(0), c.creneau_fin or time(23, 59)))

    if existante is not None:
        db.delete(existante)                     # livraisons supprimées en cascade
        db.flush()
    tournee = Tournee(date_tournee=jour, livreur_id=livreur.id, vehicule_id=vehicule.id,
                      zone_id=zone_id, max_clients=limite,
                      heure_depart=_heure(p.depart_s), heure_fin=_heure(p.fin_s))
    db.add(tournee)
    db.flush()
    temps_s, _ = estimer_temps_service_s(clients, reglages)
    for c, t in zip(clients, temps_s):
        db.add(Livraison(tournee_id=tournee.id, client_id=c.id_client, temps_service_predit_s=t))
    db.flush()
    acteur.tracer(db, "ADMIN_ATTRIBUTION_TOURNEE", cible=livreur.identifiant, tournee_id=tournee.id,
                  nb_clients=len(ids), date=jour.isoformat(), remplacee=existante is not None)
    db.commit()
    return tournee


def avertissements_tournee(db: Session, reglages: Settings, tournee: Tournee) -> list[str]:
    """Signaux de faisabilité (sans bloquer) : créneaux surchargés, départ trop matinal."""
    lignes = db.execute(select(Client, Livraison.temps_service_predit_s)
                        .join(Livraison, Livraison.client_id == Client.id_client)
                        .where(Livraison.tournee_id == tournee.id, Client.latitude.is_not(None))).all()
    clients = [c for c, _ in lignes]
    debut, fin = (en_secondes(tournee.heure_depart or reglages.heure_debut_tournee),
                  en_secondes(tournee.heure_fin or reglages.heure_fin_tournee))
    f = faisabilite(clients, Periode("tournee", debut, fin, fin), reglages, [t for _, t in lignes])
    messages = []
    if f.refuses:
        messages.append(f"{len(f.refuses)} client(s) au-delà de {reglages.clients_max_par_heure} par heure sur leur "
                        f"créneau ({', '.join(f.refuses)}) : risque de retard, répartissez-les sur une autre tournée.")
    if f.depart_au_plus_tard_s is not None and f.depart_au_plus_tard_s < debut:
        messages.append(f"Départ du dépôt nécessaire avant {en_heure(max(f.depart_au_plus_tard_s, 0))}, avant le "
                        f"début de la période ({en_heure(debut)}) : premiers créneaux trop tôt ou trop éloignés.")
    return messages


def _tournee_modifiable(db: Session, reglages: Settings, tournee_id: int) -> Tournee:
    tournee = db.get(Tournee, tournee_id)
    if tournee is None:
        raise ErreurAdmin(404, "Tournée introuvable.")
    if tournee.statut != StatutTournee.PLANIFIEE:
        raise ErreurAdmin(409, "La tournée a commencé : son livreur ne peut plus être changé.")
    if tournee.date_tournee < aujourdhui(reglages):
        raise ErreurAdmin(409, "Tournée d'une date passée : elle ne peut plus être modifiée.")
    return tournee


def _oublier_optimisation(db: Session, tournee: Tournee) -> None:
    """Le départ (domicile ou dépôt) et le camion dépendent du livreur : l'ordre et les heures
    prévues sont à recalculer par le nouveau livreur (bouton « Optimiser »)."""
    tournee.optimisee_le = tournee.distance_totale_km = tournee.duree_estimee_min = None
    for l in db.scalars(select(Livraison).where(Livraison.tournee_id == tournee.id)):
        l.ordre_passage, l.heure_arrivee_prevue = None, None


def desattribuer_tournee(db: Session, acteur: Acteur, reglages: Settings, tournee_id: int) -> Tournee:
    """Annule l'attribution : la tournée reste (clients, date, période) mais « en attente »,
    sans livreur ; elle disparaît de l'application de l'ancien livreur."""
    tournee = _tournee_modifiable(db, reglages, tournee_id)
    if tournee.livreur_id is None:
        raise ErreurAdmin(409, "Cette tournée est déjà en attente d'un livreur.")
    ancien = db.get(Utilisateur, tournee.livreur_id)
    tournee.livreur_id = tournee.vehicule_id = None
    _oublier_optimisation(db, tournee)
    acteur.tracer(db, "ADMIN_DESATTRIBUTION_TOURNEE", cible=ancien.identifiant, tournee_id=tournee.id,
                  date=tournee.date_tournee.isoformat())
    db.commit()
    return tournee


def reattribuer_tournee(db: Session, acteur: Acteur, reglages: Settings, tournee_id: int,
                        livreur_id: int) -> Tournee:
    """Donne une tournée (en attente, ou d'un autre livreur) à un livreur : même contrôles qu'à
    la création (compte actif, camion compatible avec chaque client, une tournée par jour)."""
    tournee = _tournee_modifiable(db, reglages, tournee_id)
    if tournee.livreur_id == livreur_id:
        raise ErreurAdmin(409, "Cette tournée est déjà attribuée à ce livreur.")
    livreur = db.get(Utilisateur, livreur_id)
    if livreur is None or livreur.role != RoleUtilisateur.LIVREUR or not livreur.actif:
        raise ErreurAdmin(404, "Livreur introuvable ou désactivé.")
    vehicule = db.get(Vehicule, livreur.vehicule_id) if livreur.vehicule_id else None
    if vehicule is None:
        raise ErreurAdmin(409, f"{livreur.identifiant} n'a pas de véhicule : affectez-lui un camion.")
    autre = db.scalar(select(Tournee).where(Tournee.livreur_id == livreur.id,
                                            Tournee.date_tournee == tournee.date_tournee))
    if autre is not None:
        raise ErreurAdmin(409, f"{livreur.prenom} {livreur.nom} a déjà une tournée le "
                          f"{tournee.date_tournee:%d/%m/%Y} : retirez-la d'abord ou choisissez un autre livreur.")
    ids = list(db.scalars(select(Livraison.client_id).where(Livraison.tournee_id == tournee.id)))
    _controler_clients(db, ids, vehicule)                     # camion hayon / normal
    ancien = db.get(Utilisateur, tournee.livreur_id) if tournee.livreur_id else None
    tournee.livreur_id, tournee.vehicule_id = livreur.id, vehicule.id
    _oublier_optimisation(db, tournee)
    acteur.tracer(db, "ADMIN_REATTRIBUTION_TOURNEE", cible=livreur.identifiant, tournee_id=tournee.id,
                  ancien=ancien.identifiant if ancien else None, date=tournee.date_tournee.isoformat())
    db.commit()
    return tournee


def ajouter_client_existant(db: Session, acteur: Acteur, reglages: Settings, tournee_id: int,
                            id_client: str) -> Livraison:
    """Ajout d'un client déjà en base à une tournée (réservé à l'administrateur : un livreur ne
    peut saisir qu'une nouvelle adresse, pour ne pas lire les digicodes d'autres clients)."""
    tournee = db.get(Tournee, tournee_id)
    if tournee is None:
        raise ErreurAdmin(404, "Tournée introuvable.")
    if tournee.statut == StatutTournee.TERMINEE:
        raise ErreurAdmin(409, "La tournée est terminée.")
    vehicule = db.get(Vehicule, tournee.vehicule_id) if tournee.vehicule_id else None
    if vehicule is None:
        raise ErreurAdmin(409, "Tournée en attente : attribuez-la d'abord à un livreur.")
    (client,) = _controler_clients(db, [id_client], vehicule)
    if _deja_planifies(db, tournee.date_tournee, [id_client]):
        raise ErreurAdmin(409, "Ce client est déjà dans une tournée ce jour-là.")
    nb = db.scalar(select(func.count()).select_from(Livraison).where(Livraison.tournee_id == tournee.id))
    if nb >= tournee.max_clients:
        raise ErreurAdmin(409, f"Limite de {tournee.max_clients} clients atteinte pour cette tournée.")
    livraison = Livraison(tournee_id=tournee.id, client_id=client.id_client, ajout_a_la_volee=True,
                          temps_service_predit_s=estimer_temps_service_s([client], reglages)[0][0])
    db.add(livraison)
    db.flush()
    acteur.tracer(db, "ADMIN_AJOUT_CLIENT", tournee_id=tournee.id, client_id=client.id_client)
    db.commit()
    return livraison


# =========================================================================== clients
def rechercher_clients(db: Session, texte: str | None, a_corriger: bool, limite: int):
    requete = select(Client, Zone.nom).outerjoin(Zone, Zone.id == Client.zone_id)
    if texte:
        t = texte.strip()
        requete = requete.where(or_(Client.id_client.startswith(t, autoescape=True),
                                    Client.nom_client.icontains(t, autoescape=True),
                                    Client.adresse.icontains(t, autoescape=True),
                                    Client.code_postal.startswith(t, autoescape=True),
                                    Client.ville.icontains(t, autoescape=True)))
    if a_corriger:
        # Position approximative (centre de la commune) ou introuvable : clients réels uniquement
        requete = requete.where(Client.source != SourceClient.SIMULATION, Client.actif.is_(True),
                                or_(Client.latitude.is_(None),
                                    Client.geocode_precision.in_([PrecisionGeocodage.COMMUNE,
                                                                  PrecisionGeocodage.ECHEC])))
    return db.execute(requete.order_by(Client.source, Client.id_client).limit(limite)).all()


def corriger_client(db: Session, acteur: Acteur, id_client: str, latitude: float | None,
                    longitude: float | None, type_camion: TypeCamion | None) -> Client:
    client = db.get(Client, id_client)
    if client is None or not client.actif:
        raise ErreurAdmin(404, "Client introuvable.")
    modifie = []
    if (latitude is None) != (longitude is None):
        raise ErreurAdmin(422, "Indiquez la latitude ET la longitude.")
    if latitude is not None:
        (lat_min, lat_max), (lon_min, lon_max) = FRANCE
        if not (lat_min <= latitude <= lat_max and lon_min <= longitude <= lon_max):
            raise ErreurAdmin(422, "Position hors de France métropolitaine.")
        client.latitude = Decimal(str(round(latitude, 6)))
        client.longitude = Decimal(str(round(longitude, 6)))
        client.geocode_precision, client.geocode_le = PrecisionGeocodage.MANUELLE, maintenant()
        client.zone_id = zone_id_pour(db, client.code_postal, latitude, longitude)
        modifie.append("position")
    if type_camion is not None and type_camion != client.type_camion_requis:
        client.type_camion_requis = type_camion
        modifie.append("type_camion")
    if not modifie:
        raise ErreurAdmin(422, "Aucune modification demandée.")
    acteur.tracer(db, "ADMIN_CORRECTION_CLIENT", client_id=id_client, champs=modifie)
    db.commit()
    return client


def exporter_client(db: Session, acteur: Acteur, reglages: Settings, id_client: str) -> dict:
    """Droit d'accès / portabilité (RGPD) : toutes les données du client, déchiffrées."""
    client = db.get(Client, id_client)
    if client is None:
        raise ErreurAdmin(404, "Client introuvable.")
    fuseau = ZoneInfo(reglages.fuseau_horaire)
    passages = db.execute(select(Livraison, Tournee.date_tournee)
                          .join(Tournee, Tournee.id == Livraison.tournee_id)
                          .where(Livraison.client_id == id_client).order_by(Tournee.date_tournee)).all()

    def h(x):
        return x.astimezone(fuseau).isoformat(timespec="minutes") if x else None

    export = {
        "id_client": client.id_client, "nom": client.nom_client, "type_client": client.type_client.value,
        "adresse": client.adresse, "code_postal": client.code_postal, "ville": client.ville,
        "latitude": float(client.latitude) if client.latitude is not None else None,
        "longitude": float(client.longitude) if client.longitude is not None else None,
        "telephone": client.telephone, "digicode": client.digicode,
        "notes_livraison": client.notes_livraison,
        "creneau": (f"{client.creneau_debut:%H:%M}-{client.creneau_fin:%H:%M}"
                    if client.creneau_debut and client.creneau_fin else None),
        "type_camion_requis": client.type_camion_requis.value,
        "immeuble": client.est_immeuble, "etage": client.etage, "ascenseur": client.ascenseur,
        "source": client.source.value, "actif": client.actif,
        "cree_le": h(client.cree_le), "derniere_livraison_le": h(client.derniere_livraison_le),
        "livraisons": [{"date": d.isoformat(), "statut": l.statut.value,
                        "debut": h(l.heure_debut_livraison), "fin": h(l.heure_fin_livraison)}
                       for l, d in passages],
        "remarques": [{"date": h(r.cree_le), "texte": r.texte}
                      for r in toutes_les_remarques(db, id_client)],
    }
    acteur.tracer(db, "RGPD_EXPORT", client_id=id_client)
    db.commit()
    return export


def anonymiser_client(db: Session, acteur: Acteur, reglages: Settings, id_client: str) -> Client:
    """Droit à l'effacement (RGPD). Les livraisons passées restent (statistiques, temps de
    service), mais plus rien ne permet d'identifier le client : nom, adresse précise, téléphone,
    digicode, notes et position sont effacés, ainsi que l'adresse dans le cache de géocodage."""
    client = db.get(Client, id_client)
    if client is None:
        raise ErreurAdmin(404, "Client introuvable.")
    if not client.actif and client.nom_client == "CLIENT ANONYMISE":
        raise ErreurAdmin(409, "Client déjà anonymisé.")
    a_venir = db.scalar(select(Livraison.id).join(Tournee, Tournee.id == Livraison.tournee_id)
                        .where(Livraison.client_id == id_client,
                               Tournee.date_tournee >= aujourdhui(reglages),
                               Livraison.statut.in_([StatutLivraison.A_LIVRER, StatutLivraison.EN_COURS]))
                        .limit(1))
    if a_venir is not None:
        raise ErreurAdmin(409, "Ce client a une livraison prévue : retirez-le de la tournée d'abord.")
    if client.voie:
        db.execute(delete(CacheGeocodage).where(
            CacheGeocodage.cle == cle_cache(client.voie, client.code_postal, client.ville)))
    client.nom_client = "CLIENT ANONYMISE"
    client.adresse, client.voie = f"{client.code_postal} {client.ville}", None
    client.telephone = client.digicode = client.notes_livraison = None
    client.latitude = client.longitude = client.geocode_precision = client.geocode_le = None
    client.est_immeuble = client.etage = client.ascenseur = None
    client.actif = False
    nb_remarques = effacer_remarques_client(db, id_client)      # texte libre : peut identifier
    acteur.tracer(db, "RGPD_ANONYMISATION", client_id=id_client, remarques_effacees=nb_remarques)
    db.commit()
    return client


# =========================================================================== tableau de bord
@dataclass
class TableauDeBord:
    debut: date
    fin: date
    nb_tournees: int = 0
    nb_livraisons: int = 0
    statuts: dict[str, int] = field(default_factory=dict)
    creneaux_controles: int = 0
    taux_respect_creneaux: float | None = None
    taux_remplissage: float | None = None
    pointages_retenus: int = 0
    ecart_moyen_predit_min: float | None = None     # |réel - prédit| moyen
    biais_predit_min: float | None = None           # réel - prédit (positif : sous-estimé)
    ecart_moyen_fixe_min: float | None = None       # |réel - valeur fixe 10 / 20 min|
    alertes_connexion: int = 0
    echecs_connexion: int = 0
    comptes_verrouilles: int = 0
    par_jour: list[dict] = field(default_factory=list)
    par_tournee: list[dict] = field(default_factory=list)


def _dans_creneau(debut: datetime, c: Client, fuseau: ZoneInfo) -> bool:
    if c.creneau_debut is None or c.creneau_fin is None:
        return True                              # pas de créneau imposé
    heure = debut.astimezone(fuseau).time()
    return c.creneau_debut <= heure <= c.creneau_fin


def _pourcent(numerateur: float, denominateur: float) -> float | None:
    return round(100 * numerateur / denominateur, 1) if denominateur else None


def tableau_de_bord(db: Session, reglages: Settings, jours: int = 30) -> TableauDeBord:
    fin = aujourdhui(reglages)
    debut = fin - timedelta(days=jours - 1)
    fuseau = ZoneInfo(reglages.fuseau_horaire)
    td = TableauDeBord(debut=debut, fin=fin)

    tournees = db.execute(select(Tournee, Utilisateur.identifiant)
                          .join(Utilisateur, Utilisateur.id == Tournee.livreur_id)
                          .where(Tournee.date_tournee.between(debut, fin))
                          .order_by(Tournee.date_tournee, Utilisateur.identifiant)).all()
    lignes = db.execute(select(Livraison, Client, Tournee.date_tournee)
                        .join(Client, Client.id_client == Livraison.client_id)
                        .join(Tournee, Tournee.id == Livraison.tournee_id)
                        .where(Tournee.date_tournee.between(debut, fin), Tournee.livreur_id.is_not(None))).all()
    td.nb_tournees, td.nb_livraisons = len(tournees), len(lignes)
    td.statuts = dict(Counter(l.statut.value for l, _, _ in lignes))

    # 1. Respect des créneaux : heure d'arrivée (Démarrer) dans le créneau du client
    par_jour = defaultdict(lambda: {"livraisons": 0, "controles": 0, "dans_creneau": 0, "remplissage": []})
    for l, c, jour in lignes:
        par_jour[jour]["livraisons"] += 1
        if l.statut in ISSUES_POSSIBLES and l.heure_debut_livraison is not None:
            ok = _dans_creneau(l.heure_debut_livraison, c, fuseau)
            par_jour[jour]["controles"] += 1
            par_jour[jour]["dans_creneau"] += ok
    td.creneaux_controles = sum(j["controles"] for j in par_jour.values())
    td.taux_respect_creneaux = _pourcent(sum(j["dans_creneau"] for j in par_jour.values()),
                                         td.creneaux_controles)

    # 2. Remplissage : clients de la tournée / limite d'effectif de la zone
    nb_par_tournee = Counter(l.tournee_id for l, _, _ in lignes)
    faites_par_tournee = Counter(l.tournee_id for l, _, _ in lignes if l.statut in ISSUES_POSSIBLES)
    taux = []
    for t, identifiant in tournees:
        remplissage = nb_par_tournee[t.id] / t.max_clients
        taux.append(remplissage)
        par_jour[t.date_tournee]["remplissage"].append(remplissage)
        td.par_tournee.append({"date": t.date_tournee.isoformat(), "livreur": identifiant,
                               "statut": t.statut.value, "clients": nb_par_tournee[t.id],
                               "limite": t.max_clients, "faites": faites_par_tournee[t.id],
                               "remplissage": round(100 * remplissage, 1),
                               "distance_km": float(t.distance_totale_km) if t.distance_totale_km else None})
    td.taux_remplissage = round(100 * mean(taux), 1) if taux else None

    # 3. Temps de service : réel (pointage) contre prédit et contre valeurs fixes
    pointages = [(l, c) for l, c, _ in lignes if l.statut == StatutLivraison.LIVRE
                 and l.temps_service_reel_s is not None
                 and DUREE_PLAUSIBLE_S[0] <= l.temps_service_reel_s <= DUREE_PLAUSIBLE_S[1]]
    td.pointages_retenus = len(pointages)
    avec_prediction = [(l.temps_service_reel_s, l.temps_service_predit_s) for l, _ in pointages
                       if l.temps_service_predit_s]
    if avec_prediction:
        td.ecart_moyen_predit_min = round(mean(abs(r - p) for r, p in avec_prediction) / 60, 1)
        td.biais_predit_min = round(mean(r - p for r, p in avec_prediction) / 60, 1)
    if pointages:
        fixe = {TypeCamion.NORMAL: reglages.temps_service_normal_min * 60,
                TypeCamion.HAYON: reglages.temps_service_hayon_min * 60}
        td.ecart_moyen_fixe_min = round(mean(abs(l.temps_service_reel_s - fixe[c.type_camion_requis])
                                             for l, c in pointages) / 60, 1)

    # 4. Sécurité : alertes de l'IA, échecs de connexion, comptes verrouillés
    depuis = datetime.combine(debut, time(0), tzinfo=fuseau)
    evenements = Counter(db.scalars(select(JournalSecurite.evenement).where(
        JournalSecurite.horodatage >= depuis,
        JournalSecurite.evenement.in_(["CONNEXION_ANORMALE", "CONNEXION_ECHEC"]))))
    td.alertes_connexion, td.echecs_connexion = evenements["CONNEXION_ANORMALE"], evenements["CONNEXION_ECHEC"]
    td.comptes_verrouilles = db.scalar(select(func.count()).select_from(Utilisateur).where(
        Utilisateur.verrouille_jusqua > datetime.now(timezone.utc))) or 0

    td.par_jour = [{"date": j.isoformat(), "livraisons": v["livraisons"],
                    "respect_creneaux": _pourcent(v["dans_creneau"], v["controles"]),
                    "remplissage": round(100 * mean(v["remplissage"]), 1) if v["remplissage"] else None}
                   for j, v in sorted(par_jour.items())]
    return td
