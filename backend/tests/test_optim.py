"""Tests de la matrice des trajets et du solveur OR-Tools (sans base de données)."""
import random

import pytest

from app.optim.matrice import Point, construire_matrice, longueur_parcours
from app.optim.solver import optimiser_ordre
from app.services.distances import haversine_km

DEPOT = Point(45.75, 4.80)


def point_a_l_est(km: float) -> Point:
    """Point situé à `km` kilomètres à l'est du dépôt (même latitude)."""
    return Point(DEPOT.latitude, DEPOT.longitude + km / 77.7)


def test_matrice_symetrique_diagonale_nulle():
    points = [DEPOT, Point(45.76, 4.83), Point(45.70, 4.90)]
    m = construire_matrice(points, 1.3, 30)
    for i in range(3):
        assert m.distances_m[i][i] == 0 and m.durees_s[i][i] == 0
        for j in range(3):
            assert m.distances_m[i][j] == m.distances_m[j][i]


def test_coefficient_routier_et_vitesse():
    a, b = DEPOT, Point(45.80, 4.85)
    m = construire_matrice([a, b], 1.3, 30)
    km_route = haversine_km(a.latitude, a.longitude, b.latitude, b.longitude) * 1.3
    assert m.distances_m[0][1] == round(km_route * 1000)
    assert m.durees_s[0][1] == round(km_route / 30 * 3600)      # 30 km/h


def test_parametres_invalides():
    with pytest.raises(ValueError):
        construire_matrice([DEPOT], 0.8, 30)


def test_longueur_parcours():
    m = [[0, 1, 5], [1, 0, 2], [5, 2, 0]]
    assert longueur_parcours(m, [1, 2]) == 1 + 2 + 5
    assert longueur_parcours(m, [1, 2], retour_depot=False) == 3


def test_ordre_evident_sur_une_ligne():
    # Clients à 3 km, 1 km et 2 km du dépôt : sans retour, il faut les visiter du plus proche au plus loin
    points = [DEPOT, point_a_l_est(3), point_a_l_est(1), point_a_l_est(2)]
    m = construire_matrice(points, 1.3, 30)
    res = optimiser_ordre(m, temps_limite_s=1, retour_depot=False)
    assert res.statut == "optimise"
    assert res.ordre == [2, 3, 1]


def test_tournee_de_20_clients():
    rng = random.Random(42)
    points = [DEPOT] + [Point(45.70 + rng.random() * 0.12, 4.75 + rng.random() * 0.2)
                        for _ in range(20)]
    m = construire_matrice(points, 1.3, 30)
    res = optimiser_ordre(m, temps_limite_s=2)
    assert sorted(res.ordre) == list(range(1, 21))              # chaque client exactement une fois
    assert res.duree_calcul_s < 10                              # critère du cahier des charges
    assert res.distance_m <= longueur_parcours(m.distances_m, list(range(1, 21)))


@pytest.mark.parametrize("nb", [0, 1])
def test_cas_limites(nb):
    points = [DEPOT] + [point_a_l_est(1)] * nb
    res = optimiser_ordre(construire_matrice(points, 1.3, 30))
    assert res.ordre == list(range(1, nb + 1))
