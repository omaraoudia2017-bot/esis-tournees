"""Tests du modèle CVRPTW : créneaux horaires, temps de service, limite d'effectif."""
import random
from datetime import time

import pytest

from app.optim.cvrptw import optimiser_tournees
from app.optim.matrice import MatriceTrajets, Point, construire_matrice
from app.optim.probleme import (ClientTournee, ProblemeTournees, en_heure, en_secondes,
                                evaluer_parcours)

H = 3600
DEPOT = Point(45.75, 4.80)


def a_l_est(km: float) -> Point:
    return Point(DEPOT.latitude, DEPOT.longitude + km / 77.7)


def client(ident, debut="07:00", fin="22:00", service_min=10) -> ClientTournee:
    return ClientTournee(ident, en_secondes(debut), en_secondes(fin), service_min * 60)


def probleme(points, clients, **options) -> ProblemeTournees:
    return ProblemeTournees(clients=clients, matrice=construire_matrice([DEPOT] + points, 1.3, 30),
                            **options)


# --------------------------------------------------------------------------- utilitaires
def test_conversions_heures():
    assert en_secondes("08:30") == en_secondes(time(8, 30)) == 30600
    assert en_heure(30600) == "08:30"


def test_matrice_incoherente_refusee():
    with pytest.raises(ValueError):
        ProblemeTournees(clients=[client("a")], matrice=MatriceTrajets([[0]], [[0]]))


def test_evaluation_attente_service_et_retard():
    # Matrice « à la main » : 10 min dépôt->1, 20 min 1->2
    m = MatriceTrajets(distances_m=[[0, 5000, 0], [5000, 0, 10000], [0, 10000, 0]],
                       durees_s=[[0, 600, 1800], [600, 0, 1200], [1800, 1200, 0]])
    p = ProblemeTournees(clients=[client("a", "08:00", "09:00", 10), client("b", "07:00", "07:30", 5)],
                         matrice=m, heure_depart_s=7 * H)
    ev = evaluer_parcours(p, [1, 2])
    a, b = ev.passages
    assert (a.arrivee_s, a.attente_s, a.retard_s) == (8 * H, 50 * 60, 0)   # arrivé 7h10, attend 8h00
    assert b.arrivee_s == 8 * H + 10 * 60 + 20 * 60                          # service 10 min + trajet 20 min
    assert b.retard_s == b.arrivee_s - en_secondes("07:30")
    assert ev.retour_depot_s == b.arrivee_s + 5 * 60 + 1800
    assert ev.nb_retards == 1


# --------------------------------------------------------------------------- solveur
def test_les_creneaux_imposent_l_ordre():
    # Le client le plus proche n'est disponible que l'après-midi : il doit passer en dernier
    points = [a_l_est(1), a_l_est(5), a_l_est(3)]
    clients = [client("proche", "14:00", "15:00"), client("loin", "07:00", "09:00"),
               client("milieu", "09:00", "11:00")]
    res = optimiser_tournees(probleme(points, clients), temps_limite_s=1)
    assert res.tournees[0] == [2, 3, 1]
    assert res.nb_retards == 0 and res.non_servis == []


def test_limite_d_effectif_contrainte_dure():
    points = [a_l_est(k) for k in range(1, 6)]
    clients = [client(f"c{k}") for k in range(5)]
    res = optimiser_tournees(probleme(points, clients, max_clients=3), temps_limite_s=1)
    assert len(res.tournees[0]) == 3 and len(res.non_servis) == 2      # 2 clients à reporter

    res = optimiser_tournees(probleme(points, clients, max_clients=3, nb_vehicules=2), temps_limite_s=1)
    assert res.non_servis == [] and all(len(t) <= 3 for t in res.tournees)


def test_temps_de_service_pris_en_compte():
    points = [a_l_est(2), a_l_est(2)]                                  # même adresse
    clients = [client("a", service_min=20), client("b", service_min=20)]
    res = optimiser_tournees(probleme(points, clients), temps_limite_s=1)
    premier, second = res.evaluations[0].passages
    assert second.arrivee_s - premier.arrivee_s >= 20 * 60


def test_client_impossible_a_servir_est_reporte():
    points = [a_l_est(2), a_l_est(3)]
    clients = [client("ok"), client("trop_tard", "23:00", "23:30")]      # après la fin de journée
    res = optimiser_tournees(probleme(points, clients), temps_limite_s=1)
    assert res.tournees[0] == [1] and res.non_servis == [2]


def test_retard_accepte_plutot_que_client_abandonne():
    # Créneau intenable (07:00-07:05 à 40 km) : le client est servi en retard, pas abandonné
    points = [a_l_est(40)]
    res = optimiser_tournees(probleme(points, [client("loin", "07:00", "07:05")]), temps_limite_s=1)
    assert res.non_servis == [] and res.nb_retards == 1


def test_20_clients_avec_creneaux_en_moins_de_10_s():
    rng = random.Random(3)
    creneaux = [("08:00", "10:00"), ("10:00", "12:00"), ("14:00", "16:00"), ("16:00", "18:00")]
    points = [Point(45.72 + rng.random() * 0.08, 4.78 + rng.random() * 0.12) for _ in range(20)]
    clients = [client(f"c{k}", *rng.choice(creneaux)) for k in range(20)]
    p = probleme(points, clients, max_clients=20)
    res = optimiser_tournees(p, temps_limite_s=3)
    assert res.duree_calcul_s < 10
    assert res.non_servis == [] and sorted(res.tournees[0]) == list(range(1, 21))
    # Beaucoup mieux qu'un ordre qui ignore les créneaux
    assert res.retard_total_s <= evaluer_parcours(p, list(range(1, 21))).retard_total_s


# --------------------------------------------------------------------------- Jour 6
def test_client_hayon_jamais_dans_un_camion_normal():
    points = [a_l_est(2), a_l_est(3), a_l_est(4)]
    clients = [client("n1"), ClientTournee("h1", 7 * H, 22 * H, 1200, "hayon"), client("n2")]
    p = probleme(points, clients, nb_vehicules=2, types_vehicules=["normal", "hayon"])
    res = optimiser_tournees(p, temps_limite_s=1)
    assert res.non_servis == []
    assert sorted(res.tournees[0]) == [1, 3] and res.tournees[1] == [2]


def test_sans_camion_hayon_le_client_hayon_est_reporte():
    points = [a_l_est(2), a_l_est(3)]
    clients = [client("n1"), ClientTournee("h1", 7 * H, 22 * H, 1200, "hayon")]
    p = probleme(points, clients, nb_vehicules=1, types_vehicules=["normal"])
    res = optimiser_tournees(p, temps_limite_s=1)
    assert res.tournees[0] == [1] and res.non_servis == [2]


def test_la_penalite_de_retard_arbitre_distance_et_creneaux():
    # Sans pénalité : le solveur prend le plus court chemin et accumule les retards
    points = [a_l_est(8), a_l_est(1), a_l_est(4)]
    clients = [client("loin", "07:00", "07:40"), client("proche", "10:00", "10:30"),
               client("milieu", "07:00", "08:00")]
    sans = optimiser_tournees(probleme(points, clients, penalite_retard_par_seconde=0), 1)
    avec = optimiser_tournees(probleme(points, clients, penalite_retard_par_seconde=10), 1)
    assert avec.retard_total_s <= sans.retard_total_s
    assert avec.nb_retards == 0


def test_un_camion_de_plus_seulement_s_il_evite_des_retards():
    # Deux clients opposés, tous deux à livrer avant 07:30 : un seul camion serait en retard
    points = [a_l_est(10), Point(DEPOT.latitude, DEPOT.longitude - 10 / 77.7)]
    clients = [client("est", "07:00", "07:30"), client("ouest", "07:00", "07:30")]
    res = optimiser_tournees(probleme(points, clients, nb_vehicules=2, cout_fixe_vehicule=3600), 1)
    assert sum(1 for t in res.tournees if t) == 2 and res.nb_retards == 0

    # Si le camion supplémentaire coûte très cher, on accepte le retard avec un seul camion
    # (coût fixe < pénalité « client non servi », sinon le solveur préférerait ne servir personne)
    res = optimiser_tournees(probleme(points, clients, nb_vehicules=2, cout_fixe_vehicule=10**5), 1)
    assert sum(1 for t in res.tournees if t) == 1 and res.nb_retards == 1
