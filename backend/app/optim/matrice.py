"""Matrice des distances et des temps de trajet entre tous les points d'une tournée.

Distance route estimée = distance à vol d'oiseau (Haversine) × coefficient routier.
Temps = distance route / vitesse moyenne. Aucun service cartographique payant.
Le point d'indice 0 est toujours le dépôt.
"""
from dataclasses import dataclass

from app.services.distances import haversine_km


@dataclass(frozen=True)
class Point:
    latitude: float
    longitude: float


@dataclass
class MatriceTrajets:
    distances_m: list[list[int]]   # entiers : OR-Tools travaille en nombres entiers
    durees_s: list[list[int]]

    @property
    def taille(self) -> int:
        return len(self.distances_m)


def construire_matrice(points: list[Point], coefficient_routier: float,
                       vitesse_kmh: float) -> MatriceTrajets:
    if vitesse_kmh <= 0 or coefficient_routier < 1:
        raise ValueError("vitesse > 0 et coefficient routier >= 1 attendus")
    n = len(points)
    distances = [[0] * n for _ in range(n)]
    durees = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):                  # matrice symétrique : moitié du calcul
            km = haversine_km(points[i].latitude, points[i].longitude,
                              points[j].latitude, points[j].longitude) * coefficient_routier
            distances[i][j] = distances[j][i] = round(km * 1000)
            durees[i][j] = durees[j][i] = round(km / vitesse_kmh * 3600)
    return MatriceTrajets(distances, durees)


def longueur_parcours(matrice: list[list[int]], ordre: list[int], retour_depot: bool = True) -> int:
    """Somme des arcs dépôt -> ordre[0] -> ... -> ordre[-1] (-> dépôt)."""
    chemin = [0, *ordre] + ([0] if retour_depot else [])
    return sum(matrice[a][b] for a, b in zip(chemin, chemin[1:]))


def construire_matrice_depart(depart: Point, depot: Point, clients: list[Point],
                              coefficient_routier: float, vitesse_kmh: float,
                              arrivee: Point | None = None) -> MatriceTrajets:
    """Matrice avec un point de départ et un point d'arrivée qui peuvent différer du dépôt.

    Le nœud 0 devient asymétrique : on en PART depuis `depart` (ligne 0 : position actuelle du
    livreur en cours de tournée, sinon le dépôt) et on y REVIENT à `arrivee` (colonne 0 : domicile
    du livreur interne, sinon le dépôt). Le solveur et `evaluer_parcours` restent inchangés :
    ils lisent toujours matrice[de][vers].
    """
    arrivee = arrivee or depot
    m = construire_matrice([depot, *clients], coefficient_routier, vitesse_kmh)
    if depart == depot and arrivee == depot:
        return m
    # Trajets depuis / vers les deux points particuliers (indices 0 et 1), puis les clients (2..)
    tous = construire_matrice([depart, arrivee, *clients], coefficient_routier, vitesse_kmh)
    m.distances_m[0][0], m.durees_s[0][0] = tous.distances_m[0][1], tous.durees_s[0][1]
    for j in range(1, m.taille):
        m.distances_m[0][j] = tous.distances_m[0][j + 1]      # départ -> client j
        m.durees_s[0][j] = tous.durees_s[0][j + 1]
        m.distances_m[j][0] = tous.distances_m[j + 1][1]      # client j -> arrivée
        m.durees_s[j][0] = tous.durees_s[j + 1][1]
    return m
