"""Tournées « L » (L1, L2, L3…) : préparées à l'import d'un fichier de clients, puis attribuées par
le planificateur (date, livreur, période, heure de départ) — module 4.

  - génération : les clients importés (sans doublon) sont découpés en tournées réalisables
    (services/repartition.py) et rangés par ordre horaire ; un client déjà présent dans une tournée L
    pas encore attribuée n'est pas repris ;
  - ajustement : retirer un client, le déplacer vers une autre tournée L (même type de camion),
    supprimer une tournée L ;
  - attribution : création de la tournée datée par `creer_tournee`, avec tous ses contrôles (limite
    selon la période, camion, client déjà prévu ce jour-là, livreur déjà occupé). La tournée L garde
    le lien vers la tournée créée ;
  - annulation d'une attribution : les clients reviennent dans une tournée L (services/admin.py).
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Client, ClientTourneeL, Tournee, TourneeL, Zone
from app.models.enums import StatutTournee
from app.optim.faisabilite import limite_periode
from app.services.admin import Acteur, ErreurAdmin, creer_tournee, tournee_vers_l, _periode
from app.services.planification import Faisabilite, faisabilite
from app.services.repartition import LIMITE_PAR_DEFAUT, decouper


@dataclass
class VueL:
    l: TourneeL
    zone: Zone | None
    clients: list[Client]                 # ordre horaire
    limite: int
    f: Faisabilite


def a_attribuer(db: Session) -> list[TourneeL]:
    return list(db.scalars(select(TourneeL).where(TourneeL.tournee_id.is_(None)).order_by(TourneeL.id)))


def clients_de(db: Session, l: TourneeL) -> list[Client]:
    return list(db.scalars(select(Client).join(ClientTourneeL, ClientTourneeL.client_id == Client.id_client)
                           .where(ClientTourneeL.tournee_l_id == l.id)
                           .order_by(ClientTourneeL.ordre, ClientTourneeL.id)))


def vue(db: Session, reglages: Settings, l: TourneeL, periode: str | None = None) -> VueL:
    """Ordre horaire, heures visées, départ au plus tard et limite, pour la période stockée ou une
    autre période envisagée à l'attribution."""
    p = _periode(periode or l.periode, reglages)
    zone = db.get(Zone, l.zone_id) if l.zone_id else None
    clients = [c for c in clients_de(db, l) if c.latitude is not None]
    f = faisabilite(clients, p, reglages)
    rang = {i: k for k, i in enumerate(f.ordre)}
    clients.sort(key=lambda c: rang.get(c.id_client, len(rang)))
    limite = limite_periode(p, zone.max_clients if zone else LIMITE_PAR_DEFAUT, reglages.clients_max_par_heure)
    return VueL(l, zone, clients, limite, f)


def convertir_en_attente(db: Session, reglages: Settings) -> None:
    """Anciennes tournées « en attente » (datées, sans livreur, pas commencées) : remises en tournées L."""
    anciennes = db.scalars(select(Tournee).where(Tournee.livreur_id.is_(None),
                                                 Tournee.statut == StatutTournee.PLANIFIEE)).all()
    for t in anciennes:
        tournee_vers_l(db, reglages, t)
    if anciennes:
        db.commit()


# --------------------------------------------------------------------------- génération
def generer(db: Session, acteur: Acteur, reglages: Settings, ids_clients: list[str],
            journee_continue: bool = False) -> tuple[list[TourneeL], list[tuple[str, str]]]:
    """Retourne (tournées L créées, [(client écarté, motif)]). Un client présent plusieurs fois dans
    la liste n'est livré qu'une fois."""
    ids = list(dict.fromkeys(i.strip() for i in ids_clients if i and i.strip()))
    trouves = {c.id_client: c for c in db.scalars(select(Client).where(Client.id_client.in_(ids)))}
    deja = dict(db.execute(select(ClientTourneeL.client_id, TourneeL.id)
                           .join(TourneeL, TourneeL.id == ClientTourneeL.tournee_l_id)
                           .where(TourneeL.tournee_id.is_(None), ClientTourneeL.client_id.in_(ids))).all())
    valides, exclus = [], []
    for i in ids:
        c = trouves.get(i)
        if c is None or not c.actif:
            exclus.append((i, "client inconnu ou anonymisé"))
        elif c.latitude is None:
            exclus.append((i, "adresse non localisée (géocodage à faire ou à corriger)"))
        elif i in deja:
            exclus.append((i, f"déjà dans la tournée L{deja[i]}"))
        else:
            valides.append(c)
    zones = {z.id: z for z in db.scalars(select(Zone))}
    creees = []
    for paquet in decouper(valides, zones, reglages, journee_continue):
        f = faisabilite(paquet.clients, paquet.periode, reglages)
        l = TourneeL(periode=paquet.periode.nom, type_camion=paquet.type_camion, zone_id=paquet.zone_id)
        db.add(l)
        db.flush()
        for k, i in enumerate(f.ordre + f.refuses):
            db.add(ClientTourneeL(tournee_l_id=l.id, client_id=i, ordre=k))
        creees.append(l)
    acteur.tracer(db, "TOURNEES_L_GENEREES", nb_tournees=len(creees), nb_clients=len(valides),
                  codes=[l.code for l in creees][:50])
    db.commit()
    return creees, exclus


# --------------------------------------------------------------------------- ajustements
def tournee_l(db: Session, l_id: int) -> TourneeL:
    l = db.get(TourneeL, l_id)
    if l is None:
        raise ErreurAdmin(404, "Tournée introuvable.")
    if l.tournee_id is not None:
        raise ErreurAdmin(409, f"La tournée {l.code} est déjà attribuée.")
    return l


def deplacer_client(db: Session, acteur: Acteur, l_id: int, id_client: str, vers_id: int | None) -> None:
    """Retire un client d'une tournée L (vers_id = None) ou le déplace vers une autre tournée L."""
    l = tournee_l(db, l_id)
    ligne = db.scalar(select(ClientTourneeL).where(ClientTourneeL.tournee_l_id == l.id,
                                                   ClientTourneeL.client_id == id_client))
    if ligne is None:
        raise ErreurAdmin(404, f"Ce client n'est pas dans la tournée {l.code}.")
    cible = None
    if vers_id is not None:
        cible = tournee_l(db, vers_id)
        if cible.id == l.id:
            raise ErreurAdmin(409, "Le client est déjà dans cette tournée.")
        client = db.get(Client, id_client)
        if client.type_camion_requis.value != cible.type_camion:
            raise ErreurAdmin(409, f"{cible.code} est une tournée en camion {cible.type_camion} : ce client "
                              f"exige un camion {client.type_camion_requis.value}.")
        dernier = db.scalar(select(func.max(ClientTourneeL.ordre)).where(ClientTourneeL.tournee_l_id == cible.id))
        db.add(ClientTourneeL(tournee_l_id=cible.id, client_id=id_client, ordre=(dernier or 0) + 1))
    db.delete(ligne)
    db.flush()
    vide = not db.scalar(select(func.count()).select_from(ClientTourneeL).where(ClientTourneeL.tournee_l_id == l.id))
    acteur.tracer(db, "TOURNEE_L_MODIFIEE", tournee_l=l.code, client_id=id_client,
                  vers=cible.code if cible else None, supprimee=vide)
    if vide:
        db.delete(l)
    db.commit()


def supprimer(db: Session, acteur: Acteur, l_id: int) -> None:
    l = tournee_l(db, l_id)
    acteur.tracer(db, "TOURNEE_L_SUPPRIMEE", tournee_l=l.code)
    db.delete(l)
    db.commit()


# --------------------------------------------------------------------------- attribution
def attribuer(db: Session, acteur: Acteur, reglages: Settings, l_id: int, jour: date, livreur_id: int,
              periode: str | None = None, heure_depart: time | None = None) -> Tournee:
    l = tournee_l(db, l_id)
    code = l.code
    ids = [c.id_client for c in clients_de(db, l)]
    if not ids:
        raise ErreurAdmin(409, f"La tournée {code} ne contient aucun client.")
    tournee = creer_tournee(db, acteur, reglages, livreur_id, ids, jour, periode or l.periode,
                            remplacer=False, heure_depart=heure_depart)
    l = db.get(TourneeL, l_id)
    l.tournee_id, l.attribuee_le = tournee.id, datetime.now(timezone.utc)
    acteur.tracer(db, "TOURNEE_L_ATTRIBUEE", tournee_l=code, tournee_id=tournee.id, date=jour.isoformat())
    db.commit()
    return tournee
