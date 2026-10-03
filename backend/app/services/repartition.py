"""Répartition d'une liste de clients en tournées (aide au planificateur, module 4).

Le planificateur fournit une liste (en général le dernier import CSV, complétée par une recherche).
L'application CLASSE les clients puis PROPOSE des groupes, chacun destiné à une tournée :

  1. secteur de livraison : la zone (déduite du code postal à l'import) ;
  2. plage horaire : matin / après-midi / soirée, selon le milieu du créneau du client (même règle
     que la segmentation de l'optimisation, jour 6) ; ou journée complète ;
  3. type de camion : un client « hayon » (palette) ne peut aller que dans un camion hayon ;
  4. limite d'effectif de la zone (10 à 20 clients) : un groupe trop grand est coupé en parts
     égales, en gardant ensemble les clients d'un même code postal autant que possible, puis
     dans l'ordre d'un balayage autour du dépôt (clients voisins dans la même tournée).

Pour chaque groupe, un livreur disponible est proposé (camion du bon type, pas encore de tournée ce
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
from app.optim.segmentation import periode_de
from app.services.planification import periodes, vers_client_tournee

LIMITE_PAR_DEFAUT = 15
MAX_CLIENTS_LISTE = 500


@dataclass
class Groupe:
    zone: Zone | None
    type_camion: str
    periode: str
    clients: list[Client]
    limite: int
    livreur: Utilisateur | None = None

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


def livreurs_disponibles(db: Session, jour: date) -> list[tuple[Utilisateur, Vehicule]]:
    """Livreurs actifs, avec un camion actif, sans tournée ce jour-là."""
    occupes = select(Tournee.livreur_id).where(Tournee.date_tournee == jour)
    return db.execute(
        select(Utilisateur, Vehicule).join(Vehicule, Vehicule.id == Utilisateur.vehicule_id)
        .where(Utilisateur.role == RoleUtilisateur.LIVREUR, Utilisateur.actif.is_(True),
               Vehicule.actif.is_(True), Utilisateur.id.not_in(occupes))
        .order_by(Utilisateur.nom, Utilisateur.prenom)).all()


def proposer(db: Session, reglages: Settings, jour: date, ids: list[str],
             journee_continue: bool = False) -> Proposition:
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

    for (plage, zone_id, type_camion), membres in sorted(paquets.items(), key=cle_tri):
        zone = zones.get(zone_id)
        limite = zone.max_clients if zone else LIMITE_PAR_DEFAUT
        for part in couper(membres, limite, reglages):
            prop.groupes.append(Groupe(zone, type_camion, plage, part, limite))

    # Proposition de livreurs : un par groupe (une seule tournée par livreur et par jour)
    prop.livreurs = livreurs_disponibles(db, jour)
    libres = list(prop.livreurs)
    for g in sorted(prop.groupes, key=lambda g: -len(g.clients)):       # les plus gros groupes d'abord
        choix = next(((u, v) for u, v in libres if v.type_camion.value == g.type_camion), None)
        if choix:
            g.livreur = choix[0]
            libres.remove(choix)
    return prop
