"""Données d'un problème de tournées avec fenêtres horaires (CVRPTW), sans dépendance à OR-Tools.

Les heures sont exprimées en secondes depuis minuit (08:30 -> 30600).
`evaluer_parcours` simule un ordre de passage : elle sert à contrôler le solveur
et à mesurer les créneaux non respectés d'un ordre non optimisé.
"""
from dataclasses import dataclass, field
from datetime import time

from app.optim.matrice import MatriceTrajets

JOURNEE_S = 24 * 3600


def en_secondes(valeur: time | str) -> int:
    """time(8, 30) ou "08:30" -> 30600."""
    if isinstance(valeur, str):
        heures, minutes = valeur.split(":")
        return int(heures) * 3600 + int(minutes) * 60
    return valeur.hour * 3600 + valeur.minute * 60 + valeur.second


def en_heure(secondes: int) -> str:
    """30600 -> "08:30"."""
    secondes = int(secondes)
    return f"{secondes // 3600:02d}:{secondes % 3600 // 60:02d}"


@dataclass
class ClientTournee:
    identifiant: str
    debut_s: int                 # ouverture du créneau
    fin_s: int                   # fermeture du créneau (dépassement = retard pénalisé)
    temps_service_s: int         # temps passé sur place
    type_requis: str = "normal"  # "normal" ou "hayon" (segmentation de flotte)


@dataclass
class ProblemeTournees:
    clients: list[ClientTournee]           # le client i correspond au nœud i + 1 de la matrice
    matrice: MatriceTrajets                # nœud 0 = dépôt
    nb_vehicules: int = 1
    max_clients: int = 20                  # limite d'effectif de la zone (contrainte dure)
    heure_depart_s: int = 7 * 3600
    heure_fin_s: int = 22 * 3600
    penalite_retard_par_seconde: int = 10
    penalite_client_non_servi: int = 1_000_000
    cout_fixe_vehicule: int = 0            # coût d'utilisation d'un véhicule (en secondes de trajet)
    # Type de chaque véhicule ("normal" / "hayon"). None = tous les véhicules acceptent tous les clients.
    types_vehicules: list[str] | None = None

    def __post_init__(self):
        if self.matrice.taille != len(self.clients) + 1:
            raise ValueError("la matrice doit contenir le dépôt + un nœud par client")
        if self.nb_vehicules < 1 or self.max_clients < 1:
            raise ValueError("au moins un véhicule et une capacité positive")
        if self.types_vehicules is not None and len(self.types_vehicules) != self.nb_vehicules:
            raise ValueError("un type par véhicule attendu")
        if self.cout_fixe_vehicule >= self.penalite_client_non_servi:
            # Sinon le solveur préférerait laisser tous les clients « à reporter »
            raise ValueError("le coût fixe d'un véhicule doit rester inférieur à la pénalité "
                             "d'un client non servi")

    def vehicules_compatibles(self, client: ClientTournee) -> list[int]:
        if self.types_vehicules is None:
            return list(range(self.nb_vehicules))
        return [v for v, t in enumerate(self.types_vehicules) if t == client.type_requis]


@dataclass
class Passage:
    noeud: int                   # 1..n
    arrivee_s: int               # début du service (après attente éventuelle)
    attente_s: int
    retard_s: int


@dataclass
class ParcoursEvalue:
    passages: list[Passage] = field(default_factory=list)
    depart_s: int = 0
    retour_depot_s: int = 0
    distance_m: int = 0
    duree_trajet_s: int = 0

    @property
    def nb_retards(self) -> int:
        return sum(p.retard_s > 0 for p in self.passages)

    @property
    def retard_total_s(self) -> int:
        return sum(p.retard_s for p in self.passages)

    @property
    def attente_totale_s(self) -> int:
        return sum(p.attente_s for p in self.passages)


def evaluer_parcours(probleme: ProblemeTournees, ordre: list[int]) -> ParcoursEvalue:
    """Heures de passage si le livreur suit `ordre` (nœuds 1..n), en attendant l'ouverture
    du créneau quand il arrive trop tôt."""
    m = probleme.matrice
    res = ParcoursEvalue(depart_s=probleme.heure_depart_s)
    t, precedent, service_precedent = probleme.heure_depart_s, 0, 0
    for noeud in ordre:
        client = probleme.clients[noeud - 1]
        t += service_precedent + m.durees_s[precedent][noeud]
        res.distance_m += m.distances_m[precedent][noeud]
        res.duree_trajet_s += m.durees_s[precedent][noeud]
        attente = max(0, client.debut_s - t)
        t += attente
        res.passages.append(Passage(noeud, t, attente, max(0, t - client.fin_s)))
        precedent, service_precedent = noeud, client.temps_service_s
    res.retour_depot_s = t + service_precedent + m.durees_s[precedent][0]
    res.distance_m += m.distances_m[precedent][0]
    res.duree_trajet_s += m.durees_s[precedent][0]
    return res
