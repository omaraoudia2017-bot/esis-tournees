"""Découpage d'une journée de livraisons en sous-problèmes indépendants, AVANT l'optimisation.

1. Segmentation de flotte (cahier des charges v6) : clients « hayon » et clients « normal »
   sont planifiés séparément, chacun avec les véhicules de son type.
2. Périodes (matin / après-midi / soirée) : un client rejoint la période qui contient le milieu
   de son créneau. Cela évite des journées de 7 h à 22 h avec plusieurs heures d'attente.
3. Flotte : un véhicule de réserve est proposé au solveur ; il ne l'utilise que si cela évite
   des retards qui coûtent plus cher que le coût fixe d'un camion supplémentaire.

Fonctions pures, testées dans tests/test_segmentation.py.
"""
import math
from dataclasses import dataclass

from app.optim.probleme import ClientTournee


@dataclass(frozen=True)
class Periode:
    nom: str
    depart_s: int          # départ du dépôt
    fin_s: int             # retour au dépôt au plus tard
    jusqua_s: int          # un créneau dont le milieu est avant cette heure appartient à la période


@dataclass
class Segment:
    type_camion: str
    periode: Periode
    indices: list[int]            # positions des clients dans la liste d'origine
    nb_vehicules: int = 0


def periode_de(client: ClientTournee, periodes: list[Periode]) -> Periode:
    """Rattachement par le milieu du créneau (périodes triées dans l'ordre de la journée)."""
    milieu = (client.debut_s + client.fin_s) / 2
    for periode in periodes:
        if milieu < periode.jusqua_s:
            return periode
    return periodes[-1]


def segmenter(clients: list[ClientTournee], periodes: list[Periode]) -> list[Segment]:
    """Regroupe les clients par (type de camion, période). Une seule période = journée continue."""
    groupes: dict[tuple[str, str], list[int]] = {}
    par_nom = {p.nom: p for p in periodes}
    for i, c in enumerate(clients):
        groupes.setdefault((c.type_requis, periode_de(c, periodes).nom), []).append(i)
    ordre = {p.nom: rang for rang, p in enumerate(periodes)}
    return [Segment(type_camion, par_nom[nom], indices)
            for (type_camion, nom), indices in sorted(groupes.items(),
                                                      key=lambda g: (ordre[g[0][1]], g[0][0]))]


def dimensionner(segments: list[Segment], max_clients: int, flotte: dict[str, int],
                 reserve: int = 1) -> None:
    """Véhicules proposés = clients / limite d'effectif + réserve, borné par la flotte du type.
    La flotte est comptée par période (un camion du matin peut repartir l'après-midi)."""
    for seg in segments:
        disponibles = flotte.get(seg.type_camion, 0)
        besoin = math.ceil(len(seg.indices) / max_clients) + reserve
        seg.nb_vehicules = min(besoin, disponibles)


def repartir_budget(tailles: list[int], budget_total_s: float, minimum_s: float = 0.5) -> list[float]:
    """Partage le temps de calcul entre segments, proportionnellement à leur nombre de clients,
    pour que la planification complète respecte la limite de 10 s du cahier des charges."""
    if not tailles:
        return []
    utile = max(0.0, budget_total_s - minimum_s * len(tailles))
    total = sum(tailles) or 1
    return [round(minimum_s + utile * t / total, 2) for t in tailles]
