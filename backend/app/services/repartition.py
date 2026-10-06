"""Découpage d'une liste de clients en tournées RÉALISABLES (utilisé à l'import, tournées « L »).

  1. plage horaire : matin / après-midi / soirée, selon le milieu du créneau du client (même règle
     que la segmentation de l'optimisation) ; ou journée complète ;
  2. secteur (zone, déduite du code postal) et type de camion (un client « hayon » ne peut aller
     que dans un camion hayon) ;
  3. faisabilité pour le livreur : au plus N clients prévus dans une même heure (2 par défaut) et une
     limite qui dépend de la période (N x heures, sans dépasser la limite de la zone : seule une
     tournée de la journée complète atteint 20 clients). Les clients sont placés par ordre d'échéance
     (créneau qui finit le plus tôt d'abord), de préférence avec ceux du même code postal.

L'ordre géographique est ensuite optimisé par le livreur (OR-Tools).
"""
import math
from dataclasses import dataclass

from app.core.config import Settings
from app.models import Client, Zone
from app.optim.faisabilite import heures_possibles, limite_periode
from app.optim.segmentation import Periode, periode_de
from app.services.planification import periodes, vers_client_tournee

LIMITE_PAR_DEFAUT = 15


@dataclass
class Paquet:
    periode: Periode
    zone_id: int | None
    type_camion: str
    clients: list[Client]


def _angle(c: Client, reglages: Settings) -> float:
    """Angle du client vu du dépôt (balayage) : des clients d'angles proches sont voisins."""
    dy = float(c.latitude) - reglages.depot_latitude
    dx = (float(c.longitude) - reglages.depot_longitude) * math.cos(math.radians(reglages.depot_latitude))
    return math.atan2(dy, dx)


def emballer(clients: list[Client], periode: Periode, limite: int, par_heure: int,
             reglages: Settings) -> list[list[Client]]:
    """Chaque client (par ordre d'échéance) rejoint la première tournée, de préférence avec son code
    postal, qui a encore de la place au total ET dans une heure de son créneau (au plus `par_heure`
    clients par heure) ; sinon une nouvelle tournée est ouverte."""
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


def decouper(clients: list[Client], zones: dict[int, Zone], reglages: Settings,
             journee_continue: bool = False, par_heure: int | None = None) -> list[Paquet]:
    """Tournées réalisables, triées par période, zone et type de camion."""
    par_heure = par_heure or reglages.clients_max_par_heure
    plages = periodes(reglages, journee_continue)
    rang = {p.nom: k for k, p in enumerate(plages)}
    groupes: dict[tuple[str, int | None, str], list[Client]] = {}
    for c in clients:
        plage = periode_de(vers_client_tournee(c, reglages, temps_service_s=0), plages)
        groupes.setdefault((plage.nom, c.zone_id, c.type_camion_requis.value), []).append(c)
    par_nom = {p.nom: p for p in plages}
    paquets = []
    for (plage, zone_id, type_camion), membres in sorted(
            groupes.items(), key=lambda g: (rang[g[0][0]], zones[g[0][1]].nom if g[0][1] in zones else "~", g[0][2])):
        zone = zones.get(zone_id)
        limite = limite_periode(par_nom[plage], zone.max_clients if zone else LIMITE_PAR_DEFAUT, par_heure)
        for part in emballer(membres, par_nom[plage], limite, par_heure, reglages):
            paquets.append(Paquet(par_nom[plage], zone_id, type_camion, part))
    return paquets
