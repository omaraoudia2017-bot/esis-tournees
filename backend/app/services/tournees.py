"""Tournée du jour d'un livreur : consultation, optimisation en un clic, pointage terrain.

Règles (cahier des charges v6, module 2) :
  - un livreur ne voit et ne modifie que SA tournée (sinon : 404 + trace dans le journal) ;
  - « Optimiser » réordonne les clients avec le solveur CVRPTW (créneaux, temps de service,
    type de véhicule, limite d'effectif) et enregistre l'ordre et les heures de passage prévues ;
  - « Démarrer » puis « Terminer » horodatent la livraison ; PostgreSQL calcule le temps de
    service réel (heure_fin - heure_debut), donnée d'entraînement du futur modèle prédictif.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Client, Livraison, Tournee, Utilisateur, Vehicule
from app.models.enums import StatutLivraison, StatutTournee
from app.optim.cvrptw import optimiser_tournees
from app.optim.probleme import en_secondes, evaluer_parcours
from app.services.planification import construire_probleme, temps_service_theorique_s

EN_ATTENTE = (StatutLivraison.A_LIVRER, StatutLivraison.EN_COURS)
ISSUES_POSSIBLES = (StatutLivraison.LIVRE, StatutLivraison.ABSENT, StatutLivraison.REFUSE)


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
    nb_clients: int
    nb_optimises: int
    distance_avant_km: float           # mêmes clients, ordre brut (tel qu'attribué)
    distance_apres_km: float
    nb_retards: int
    duree_calcul_s: float
    a_reporter: list[str] = field(default_factory=list)


def bornes_horaires(tournee: Tournee, reglages: Settings) -> tuple[int, int]:
    depart = tournee.heure_depart or reglages.heure_debut_tournee
    fin = tournee.heure_fin or reglages.heure_fin_tournee
    return en_secondes(depart), en_secondes(fin)


def _horodatage(jour: date, secondes: int, reglages: Settings) -> datetime:
    minuit = datetime.combine(jour, time(0), tzinfo=ZoneInfo(reglages.fuseau_horaire))
    return minuit + timedelta(seconds=int(secondes))


def optimiser_tournee(db: Session, tournee: Tournee, reglages: Settings) -> ResultatOptimisation:
    if tournee.statut != StatutTournee.PLANIFIEE:
        raise ErreurTournee(409, "La tournée a déjà commencé : l'ordre de passage ne peut plus "
                                 "être recalculé entièrement.")
    lignes = livraisons_de(db, tournee)
    if not lignes:
        raise ErreurTournee(409, "La tournée ne contient aucun client.")

    # Un client « reporté » lors d'un calcul précédent est remis dans le calcul
    for livraison, _ in lignes:
        if livraison.statut == StatutLivraison.REPORTE:
            livraison.statut = StatutLivraison.A_LIVRER

    localises = [(l, c) for l, c in lignes if c.latitude is not None and c.longitude is not None]
    non_localises = [(l, c) for l, c in lignes if c.latitude is None or c.longitude is None]
    vehicule = db.get(Vehicule, tournee.vehicule_id) if tournee.vehicule_id else None
    depart_s, fin_s = bornes_horaires(tournee, reglages)

    clients = [c for _, c in localises]
    probleme = construire_probleme(
        clients, reglages, tournee.max_clients, nb_vehicules=1, depart_s=depart_s, fin_s=fin_s,
        # Segmentation de flotte : un client hayon ne peut pas être servi par un camion normal
        types_vehicules=[vehicule.type_camion.value] if vehicule else None,
    )
    res = optimiser_tournees(probleme, reglages.optim_temps_limite_s)
    ordre, evaluation = res.tournees[0], res.evaluations[0]
    # Référence : les mêmes clients, dans l'ordre brut de la liste attribuée
    avant = evaluer_parcours(probleme, sorted(ordre))

    # Clients servis : ordre de passage 1..k et heure d'arrivée prévue
    for rang, (noeud, passage) in enumerate(zip(ordre, evaluation.passages), start=1):
        livraison, client = localises[noeud - 1]
        livraison.ordre_passage = rang
        livraison.heure_arrivee_prevue = _horodatage(tournee.date_tournee, passage.arrivee_s, reglages)
        livraison.temps_service_predit_s = temps_service_theorique_s(client, reglages)

    # Clients impossibles à servir (véhicule inadapté, créneau intenable, adresse non géocodée)
    reportes = [localises[k - 1] for k in res.non_servis] + non_localises
    for rang, (livraison, _) in enumerate(reportes, start=len(ordre) + 1):
        livraison.ordre_passage = rang
        livraison.heure_arrivee_prevue = None
        livraison.statut = StatutLivraison.REPORTE

    tournee.optimisee_le = maintenant()
    tournee.distance_totale_km = round(evaluation.distance_m / 1000, 2)
    tournee.duree_estimee_min = (evaluation.retour_depot_s - evaluation.depart_s) // 60 if ordre else 0
    db.commit()

    return ResultatOptimisation(
        nb_clients=len(lignes),
        nb_optimises=len(ordre),
        distance_avant_km=round(avant.distance_m / 1000, 1),
        distance_apres_km=round(evaluation.distance_m / 1000, 1),
        nb_retards=evaluation.nb_retards,
        duree_calcul_s=res.duree_calcul_s,
        a_reporter=[c.id_client for _, c in reportes],
    )


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
