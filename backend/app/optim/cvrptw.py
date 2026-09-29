"""Résolution du CVRPTW avec OR-Tools : fenêtres horaires, temps de service, limite d'effectif.

Modèle (cahier des charges v6, module 2) :
  - coût minimisé = temps de trajet total + pénalités de retard (fenêtres « souples ») ;
  - un client n'est jamais servi avant l'ouverture de son créneau (le livreur attend) ;
  - chaque véhicule sert au plus `max_clients` clients (10 à 20 selon la zone) : contrainte DURE ;
  - un client qui ne peut pas être servi est laissé « à reporter » plutôt que de rendre
    le problème insoluble (très forte pénalité, donc uniquement en dernier recours) ;
  - retour au dépôt avant l'heure de fin de journée.
"""
import time
from dataclasses import dataclass, field

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from app.optim.probleme import JOURNEE_S, ParcoursEvalue, ProblemeTournees, evaluer_parcours


@dataclass
class ResultatTournees:
    tournees: list[list[int]]                 # une liste de nœuds (1..n) par véhicule
    evaluations: list[ParcoursEvalue]         # heures de passage, retards, distances
    non_servis: list[int] = field(default_factory=list)
    duree_calcul_s: float = 0.0
    statut: str = "optimise"

    @property
    def distance_m(self) -> int:
        return sum(e.distance_m for e in self.evaluations)

    @property
    def duree_trajet_s(self) -> int:
        return sum(e.duree_trajet_s for e in self.evaluations)

    @property
    def nb_retards(self) -> int:
        return sum(e.nb_retards for e in self.evaluations)

    @property
    def retard_total_s(self) -> int:
        return sum(e.retard_total_s for e in self.evaluations)


def optimiser_tournees(p: ProblemeTournees, temps_limite_s: int = 5) -> ResultatTournees:
    debut = time.perf_counter()
    n = len(p.clients)
    if n == 0:
        return ResultatTournees([[] for _ in range(p.nb_vehicules)],
                                [evaluer_parcours(p, []) for _ in range(p.nb_vehicules)])

    durees = p.matrice.durees_s
    service = [0] + [c.temps_service_s for c in p.clients]
    manager = pywrapcp.RoutingIndexManager(n + 1, p.nb_vehicules, 0)
    routage = pywrapcp.RoutingModel(manager)
    noeud = manager.IndexToNode

    # Coût d'un arc : temps de trajet
    def trajet(i: int, j: int) -> int:
        return durees[noeud(i)][noeud(j)]

    routage.SetArcCostEvaluatorOfAllVehicles(routage.RegisterTransitCallback(trajet))

    # Dimension « Temps » : service chez le client de départ + trajet vers le suivant
    def temps(i: int, j: int) -> int:
        a = noeud(i)
        return service[a] + durees[a][noeud(j)]

    routage.AddDimension(
        routage.RegisterTransitCallback(temps),
        JOURNEE_S,        # attente autorisée (arrivée avant l'ouverture du créneau)
        JOURNEE_S,        # horizon : une journée
        False,            # l'heure de départ n'est pas forcée à 0
        "Temps",
    )
    dim_temps = routage.GetDimensionOrDie("Temps")

    for k, client in enumerate(p.clients, start=1):
        index = manager.NodeToIndex(k)
        dim_temps.CumulVar(index).SetRange(min(client.debut_s, JOURNEE_S), JOURNEE_S)
        dim_temps.SetCumulVarSoftUpperBound(index, client.fin_s, p.penalite_retard_par_seconde)
        routage.AddDisjunction([index], p.penalite_client_non_servi)

    for v in range(p.nb_vehicules):
        dim_temps.CumulVar(routage.Start(v)).SetRange(p.heure_depart_s, p.heure_depart_s)
        dim_temps.CumulVar(routage.End(v)).SetMax(p.heure_fin_s)
        routage.AddVariableMinimizedByFinalizer(dim_temps.CumulVar(routage.End(v)))

    # Dimension « Effectif » : 1 par client, plafond = limite de la zone (contrainte dure)
    def demande(i: int) -> int:
        return 0 if noeud(i) == 0 else 1

    routage.AddDimensionWithVehicleCapacity(
        routage.RegisterUnaryTransitCallback(demande), 0,
        [p.max_clients] * p.nb_vehicules, True, "Effectif",
    )

    parametres = pywrapcp.DefaultRoutingSearchParameters()
    parametres.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    parametres.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
    parametres.time_limit.FromSeconds(temps_limite_s)

    solution = routage.SolveWithParameters(parametres)
    if solution is None:
        return ResultatTournees([[] for _ in range(p.nb_vehicules)],
                                [evaluer_parcours(p, []) for _ in range(p.nb_vehicules)],
                                non_servis=list(range(1, n + 1)),
                                duree_calcul_s=round(time.perf_counter() - debut, 2),
                                statut="echec")

    tournees = []
    for v in range(p.nb_vehicules):
        ordre, index = [], routage.Start(v)
        while not routage.IsEnd(index):
            if noeud(index) != 0:
                ordre.append(noeud(index))
            index = solution.Value(routage.NextVar(index))
        tournees.append(ordre)

    servis = {k for t in tournees for k in t}
    return ResultatTournees(
        tournees=tournees,
        # Les heures de passage sont recalculées par simulation : même règle que le solveur
        evaluations=[evaluer_parcours(p, t) for t in tournees],
        non_servis=[k for k in range(1, n + 1) if k not in servis],
        duree_calcul_s=round(time.perf_counter() - debut, 2),
    )
