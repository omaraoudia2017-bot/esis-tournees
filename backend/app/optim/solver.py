"""Solveur de tournée avec Google OR-Tools (module de routage).

Jour 4 : un véhicule, un dépôt, minimisation du temps de trajet total.
Les fenêtres horaires, la limite d'effectif, les temps de service et la segmentation
hayon / normal viendront s'ajouter à ce même modèle (dimensions OR-Tools).
"""
import time
from dataclasses import dataclass

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from app.optim.matrice import MatriceTrajets, longueur_parcours


@dataclass
class ResultatOptimisation:
    ordre: list[int]            # indices des clients (1..n) dans l'ordre de passage
    distance_m: int
    duree_trajet_s: int
    duree_calcul_s: float
    statut: str                 # "optimise" ou "echec"


def optimiser_ordre(matrice: MatriceTrajets, temps_limite_s: int = 5,
                    retour_depot: bool = True) -> ResultatOptimisation:
    """Ordre de passage minimisant le temps total de trajet (nœud 0 = dépôt)."""
    debut = time.perf_counter()
    nb_clients = matrice.taille - 1
    if nb_clients <= 1:
        ordre = list(range(1, nb_clients + 1))
        return _resultat(matrice, ordre, retour_depot, debut, "optimise")

    durees = [ligne[:] for ligne in matrice.durees_s]
    if retour_depot:
        manager = pywrapcp.RoutingIndexManager(matrice.taille, 1, 0)
    else:
        # Tournée ouverte : on ajoute une arrivée fictive atteignable gratuitement de partout
        for ligne in durees:
            ligne.append(0)
        durees.append([0] * (matrice.taille + 1))
        manager = pywrapcp.RoutingIndexManager(matrice.taille + 1, 1, [0], [matrice.taille])

    routage = pywrapcp.RoutingModel(manager)

    def cout_trajet(index_depart: int, index_arrivee: int) -> int:
        return durees[manager.IndexToNode(index_depart)][manager.IndexToNode(index_arrivee)]

    rappel = routage.RegisterTransitCallback(cout_trajet)
    routage.SetArcCostEvaluatorOfAllVehicles(rappel)

    parametres = pywrapcp.DefaultRoutingSearchParameters()
    # Solution initiale rapide (plus proche voisin amélioré), puis recherche locale guidée
    parametres.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    parametres.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
    parametres.time_limit.FromSeconds(temps_limite_s)

    solution = routage.SolveWithParameters(parametres)
    if solution is None:
        return _resultat(matrice, list(range(1, nb_clients + 1)), retour_depot, debut, "echec")

    ordre, index = [], routage.Start(0)
    while not routage.IsEnd(index):
        noeud = manager.IndexToNode(index)
        if 0 < noeud <= nb_clients:
            ordre.append(noeud)
        index = solution.Value(routage.NextVar(index))
    return _resultat(matrice, ordre, retour_depot, debut, "optimise")


def _resultat(matrice: MatriceTrajets, ordre: list[int], retour_depot: bool, debut: float,
              statut: str) -> ResultatOptimisation:
    return ResultatOptimisation(
        ordre=ordre,
        distance_m=longueur_parcours(matrice.distances_m, ordre, retour_depot),
        duree_trajet_s=longueur_parcours(matrice.durees_s, ordre, retour_depot),
        duree_calcul_s=round(time.perf_counter() - debut, 2),
        statut=statut,
    )
