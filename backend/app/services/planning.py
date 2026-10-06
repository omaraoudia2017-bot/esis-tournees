"""Planning des livreurs (module 4) : une ligne par livreur, une colonne par jour.

Chaque case montre la tournée du livreur ce jour-là (période, clients, état), sa journée de trajets
s'il n'a pas de tournée, ou rien (livreur libre). À côté, les tournées à attribuer : celles dont
l'attribution a été annulée (« en attente »), avec le type de camion qu'exigent leurs clients.
Lecture seule : l'attribution passe par les routes habituelles, avec tous leurs contrôles.
"""
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Client, EtapeTrajet, JourneeTrajets, Livraison, Tournee, Utilisateur, Vehicule, Zone
from app.models.enums import RoleUtilisateur, StatutLivraison

ISSUES = (StatutLivraison.LIVRE, StatutLivraison.ABSENT, StatutLivraison.REFUSE)


@dataclass
class CaseTournee:
    tournee: Tournee
    zone: str | None
    nb_clients: int
    nb_terminees: int


@dataclass
class Planning:
    debut: date
    fin: date
    livreurs: list[tuple[Utilisateur, Vehicule | None]]
    tournees: list[CaseTournee]
    trajets: list[tuple[JourneeTrajets, int]]          # (journée, nombre d'étapes)


def _compteurs():
    return (select(Livraison.tournee_id, func.count().label("nb"),
                   func.count().filter(Livraison.statut.in_(ISSUES)).label("faites"))
            .group_by(Livraison.tournee_id).subquery())


def planning(db: Session, debut: date, nb_jours: int = 7) -> Planning:
    fin = debut + timedelta(days=nb_jours - 1)
    livreurs = db.execute(
        select(Utilisateur, Vehicule).outerjoin(Vehicule, Vehicule.id == Utilisateur.vehicule_id)
        .where(Utilisateur.role == RoleUtilisateur.LIVREUR, Utilisateur.actif.is_(True))
        .order_by(Utilisateur.nom, Utilisateur.prenom)).all()
    nb = _compteurs()
    tournees = [CaseTournee(t, zone, n or 0, faites or 0) for t, zone, n, faites in db.execute(
        select(Tournee, Zone.nom, nb.c.nb, nb.c.faites)
        .outerjoin(Zone, Zone.id == Tournee.zone_id).outerjoin(nb, nb.c.tournee_id == Tournee.id)
        .where(Tournee.date_tournee.between(debut, fin), Tournee.livreur_id.is_not(None)))]
    etapes = (select(EtapeTrajet.journee_id, func.count().label("nb")).group_by(EtapeTrajet.journee_id).subquery())
    trajets = [(j, n or 0) for j, n in db.execute(
        select(JourneeTrajets, etapes.c.nb).outerjoin(etapes, etapes.c.journee_id == JourneeTrajets.id)
        .where(JourneeTrajets.date_journee.between(debut, fin)))]
    return Planning(debut, fin, list(livreurs), tournees, trajets)


def types_camion_requis(db: Session, tournee_ids: list[int]) -> dict[int, str]:
    """Type de camion qu'exigent les clients d'une tournée (« hayon » si au moins un client palette)."""
    if not tournee_ids:
        return {}
    types: dict[int, set[str]] = {}
    for tid, type_camion in db.execute(
            select(Livraison.tournee_id, Client.type_camion_requis).join(Client, Client.id_client == Livraison.client_id)
            .where(Livraison.tournee_id.in_(tournee_ids)).distinct()):
        types.setdefault(tid, set()).add(type_camion.value)
    return {tid: "hayon" if "hayon" in t else "normal" for tid, t in types.items()}
