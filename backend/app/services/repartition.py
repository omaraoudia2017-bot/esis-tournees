"""Répartition d'une liste de clients en tournées RÉALISABLES (aide au planificateur, module 4).

Le planificateur fournit une liste (en général le dernier import CSV, complétée par une recherche).
L'application CLASSE les clients puis PROPOSE des tournées :

  1. plage horaire : matin / après-midi / soirée, selon le milieu du créneau du client (même règle
     que la segmentation de l'optimisation) ; ou journée complète ;
  2. secteur (zone, déduite du code postal) et type de camion (un client « hayon » ne peut aller
     que dans un camion hayon) ;
  3. faisabilité pour le livreur : au plus N clients prévus dans une même heure (2 par défaut),
     et une limite qui dépend de la période (N x heures, sans dépasser la limite de la zone : seule
     une tournée de la journée complète atteint 20 clients). Les clients sont placés par ordre
     d'échéance (créneau qui finit le plus tôt d'abord), de préférence avec ceux du même code postal ;
  4. ordre horaire dans chaque tournée et heure de départ du dépôt au plus tard ; l'optimisation
     du livreur réorganise ensuite l'ordre géographique.

Pour chaque tournée, un livreur disponible est proposé (camion du bon type, pas encore de tournée ce
jour-là). Rien n'est enregistré ici : le planificateur ajuste puis valide, et chaque tournée est
créée par `creer_tournee`, avec tous ses contrôles (limite, camion, doublons, date).
"""
import math
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Client, Livraison, Tournee, Utilisateur, Vehicule, Zone
from app.models.enums import RoleUtilisateur
from app.optim.faisabilite import heures_possibles, limite_periode
from app.optim.segmentation import Periode, periode_de
from app.services.planification import faisabilite, periodes, vers_client_tournee

LIMITE_PAR_DEFAUT = 15
MAX_CLIENTS_LISTE = 500


@dataclass
class Groupe:
    zone: Zone | None
    type_camion: str
    periode: str
    clients: list[Client]                  # dans l'ordre horaire proposé
    limite: int
    livreur: Utilisateur | None = None
    heures: dict[str, int] = field(default_factory=dict)       # client -> heure visée (8 = 8 h - 9 h)
    debut_periode_s: int = 0
    depart_au_plus_tard_s: int | None = None

    @property
    def codes_postaux(self) -> list[str]:
        return sorted({c.code_postal for c in self.clients})


@dataclass
class Proposition:
    jour: date
    groupes: list[Groupe] = field(default_factory=list)
    exclus: list[tuple[str, str]] = field(default_factory=list)          # (identifiant, motif)
    livreurs: list[tuple[Utilisateur, Vehicule]] = field(default_factory=list)


def _angle(c: Client, reglages: Settings) -> float:
    """Angle du client vu du dépôt (balayage) : des clients d'angles proches sont voisins."""
    dy = float(c.latitude) - reglages.depot_latitude
    dx = (float(c.longitude) - reglages.depot_longitude) * math.cos(math.radians(reglages.depot_latitude))
    return math.atan2(dy, dx)


def couper(clients: list[Client], limite: int, reglages: Settings) -> list[list[Client]]:
    """Coupe en ceil(n / limite) parts de tailles égales (à un client près), dans l'ordre
    code postal puis balayage autour du dépôt."""
    ordre = sorted(clients, key=lambda c: (c.code_postal, _angle(c, reglages)))
    nb = max(1, math.ceil(len(ordre) / limite))
    base, reste = divmod(len(ordre), nb)
    parts, debut = [], 0
    for k in range(nb):
        taille = base + (1 if k < reste else 0)
        parts.append(ordre[debut:debut + taille])
        debut += taille
    return parts


def emballer(clients: list[Client], periode: Periode, limite: int, par_heure: int,
             reglages: Settings) -> list[list[Client]]:
    """Tournées réalisables : chaque client (par ordre d'échéance) rejoint la première tournée,
    de préférence avec son code postal, qui a encore de la place au total ET dans une heure de son
    créneau (au plus `par_heure` clients par heure) ; sinon une nouvelle tournée est ouverte."""
    en_tournee = {c.id_client: vers_client_tournee(c, reglages, temps_service_s=0) for c in clients}
    ordre = sorted(clients, key=lambda c: (en_tournee[c.id_client].fin_s, en_tournee[c.id_client].debut_s,
                                           c.code_postal, _angle(c, reglages)))
    tournees: list[dict] = []
    for c in ordre:
        heures = heures_possibles(en_tournee[c.id_client], periode)
        choix = None
        for k in sorted(range(len(tournees)), key=lambda k: (c.code_postal not in tournees[k]["cps"], k)):
            t = tournees[k]
            if len(t["clients"]) >= limite:
                continue
            h = next((h for h in heures if t["charge"].get(h, 0) < par_heure), None)
            if h is not None:
                choix = (t, h)
                break
        if choix is None:
            tournees.append({"clients": [], "charge": {}, "cps": set()})
            choix = (tournees[-1], heures[0])
        t, h = choix
        t["clients"].append(c)
        t["charge"][h] = t["charge"].get(h, 0) + 1
        t["cps"].add(c.code_postal)
    return [t["clients"] for t in tournees]


def livreurs_disponibles(db: Session, jour: date) -> list[tuple[Utilisateur, Vehicule]]:
    """Livreurs actifs, avec un camion actif, sans tournée ce jour-là."""
    # (NOT IN avec un NULL ne renverrait aucune ligne : les tournées en attente sont écartées)
    occupes = select(Tournee.livreur_id).where(Tournee.date_tournee == jour, Tournee.livreur_id.is_not(None))
    return db.execute(
        select(Utilisateur, Vehicule).join(Vehicule, Vehicule.id == Utilisateur.vehicule_id)
        .where(Utilisateur.role == RoleUtilisateur.LIVREUR, Utilisateur.actif.is_(True),
               Vehicule.actif.is_(True), Utilisateur.id.not_in(occupes))
        .order_by(Utilisateur.nom, Utilisateur.prenom)).all()


def proposer(db: Session, reglages: Settings, jour: date, ids: list[str],
             journee_continue: bool = False, par_heure: int | None = None) -> Proposition:
    par_heure = par_heure or reglages.clients_max_par_heure
    ids = list(dict.fromkeys(i.strip() for i in ids if i.strip()))[:MAX_CLIENTS_LISTE]
    prop = Proposition(jour=jour)
    trouves = {c.id_client: c for c in db.scalars(select(Client).where(Client.id_client.in_(ids)))}
    deja = set(db.scalars(select(Livraison.client_id).join(Tournee, Tournee.id == Livraison.tournee_id)
                          .where(Tournee.date_tournee == jour, Livraison.client_id.in_(ids))))

    valides = []
    for i in ids:
        c = trouves.get(i)
        if c is None or not c.actif:
            prop.exclus.append((i, "client inconnu ou anonymisé"))
        elif c.latitude is None:
            prop.exclus.append((i, "adresse non localisée (géocodage à faire ou à corriger)"))
        elif i in deja:
            prop.exclus.append((i, f"déjà dans une tournée le {jour:%d/%m/%Y}"))
        else:
            valides.append(c)

    # Classement : (période, zone, type de camion)
    plages = periodes(reglages, journee_continue)
    rang_plage = {p.nom: k for k, p in enumerate(plages)}
    zones = {z.id: z for z in db.scalars(select(Zone))}
    paquets: dict[tuple[str, int | None, str], list[Client]] = {}
    for c in valides:
        plage = periode_de(vers_client_tournee(c, reglages, temps_service_s=0), plages).nom
        paquets.setdefault((plage, c.zone_id, c.type_camion_requis.value), []).append(c)

    def cle_tri(item):
        (plage, zone_id, type_camion), _ = item
        return rang_plage[plage], zones[zone_id].nom if zone_id in zones else "~", type_camion

    par_nom = {p.nom: p for p in plages}
    for (plage, zone_id, type_camion), membres in sorted(paquets.items(), key=cle_tri):
        zone, periode = zones.get(zone_id), par_nom[plage]
        limite = limite_periode(periode, zone.max_clients if zone else LIMITE_PAR_DEFAUT, par_heure)
        for part in emballer(membres, periode, limite, par_heure, reglages):
            f = faisabilite(part, periode, reglages, par_heure=par_heure)
            rang = {i: k for k, i in enumerate(f.ordre)}
            part.sort(key=lambda c: rang.get(c.id_client, len(rang)))           # ordre horaire
            prop.groupes.append(Groupe(zone, type_camion, plage, part, limite, heures=f.heures,
                                       debut_periode_s=periode.depart_s,
                                       depart_au_plus_tard_s=f.depart_au_plus_tard_s))

    # Proposition de livreurs : un par groupe (une seule tournée par livreur et par jour)
    prop.livreurs = livreurs_disponibles(db, jour)
    libres = list(prop.livreurs)
    for g in sorted(prop.groupes, key=lambda g: -len(g.clients)):       # les plus gros groupes d'abord
        choix = next(((u, v) for u, v in libres if v.type_camion.value == g.type_camion), None)
        if choix:
            g.livreur = choix[0]
            libres.remove(choix)
    return prop
