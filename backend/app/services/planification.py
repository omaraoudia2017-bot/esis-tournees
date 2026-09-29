"""Passage des données de la base (clients, zone, flotte) aux problèmes d'optimisation."""
import time
from dataclasses import dataclass, field

from app.core.config import Settings
from app.models import Client
from app.models.enums import TypeCamion
from app.optim.cvrptw import optimiser_tournees
from app.optim.matrice import Point, construire_matrice
from app.optim.probleme import ClientTournee, ParcoursEvalue, ProblemeTournees, en_secondes
from app.optim.segmentation import Periode, Segment, dimensionner, repartir_budget, segmenter


def temps_service_theorique_s(client: Client, reglages: Settings) -> int:
    """Démarrage à froid : valeurs fixes (10 min colis, 20 min palette/hayon).
    Elles seront remplacées par les prédictions du modèle de Machine Learning."""
    minutes = (reglages.temps_service_hayon_min if client.type_camion_requis == TypeCamion.HAYON
               else reglages.temps_service_normal_min)
    return minutes * 60


def vers_client_tournee(c: Client, reglages: Settings) -> ClientTournee:
    return ClientTournee(
        identifiant=c.id_client,
        debut_s=en_secondes(c.creneau_debut) if c.creneau_debut else en_secondes(reglages.heure_debut_tournee),
        fin_s=en_secondes(c.creneau_fin) if c.creneau_fin else en_secondes(reglages.heure_fin_tournee),
        temps_service_s=temps_service_theorique_s(c, reglages),
        type_requis=c.type_camion_requis.value,
    )


def construire_probleme(clients: list[Client], reglages: Settings, max_clients: int,
                        nb_vehicules: int = 1, depart_s: int | None = None, fin_s: int | None = None,
                        types_vehicules: list[str] | None = None) -> ProblemeTournees:
    points = [Point(reglages.depot_latitude, reglages.depot_longitude)]
    points += [Point(float(c.latitude), float(c.longitude)) for c in clients]
    return ProblemeTournees(
        clients=[vers_client_tournee(c, reglages) for c in clients],
        matrice=construire_matrice(points, reglages.coefficient_routier, reglages.vitesse_moyenne_kmh),
        nb_vehicules=nb_vehicules,
        max_clients=max_clients,
        heure_depart_s=en_secondes(reglages.heure_debut_tournee) if depart_s is None else depart_s,
        heure_fin_s=en_secondes(reglages.heure_fin_tournee) if fin_s is None else fin_s,
        penalite_retard_par_seconde=reglages.penalite_retard_par_seconde,
        penalite_client_non_servi=reglages.penalite_client_non_servi,
        cout_fixe_vehicule=reglages.cout_fixe_vehicule_s,
        types_vehicules=types_vehicules,
    )


# --------------------------------------------------------------------------- journée complète
@dataclass
class TourneePlanifiee:
    type_camion: str
    periode: str
    clients: list[Client]                  # dans l'ordre de passage
    evaluation: ParcoursEvalue


@dataclass
class PlanJournee:
    tournees: list[TourneePlanifiee] = field(default_factory=list)
    a_reporter: list[Client] = field(default_factory=list)
    duree_calcul_s: float = 0.0

    @property
    def distance_m(self) -> int:
        return sum(t.evaluation.distance_m for t in self.tournees)

    @property
    def nb_retards(self) -> int:
        return sum(t.evaluation.nb_retards for t in self.tournees)

    @property
    def attente_totale_s(self) -> int:
        return sum(t.evaluation.attente_totale_s for t in self.tournees)

    @property
    def nb_servis(self) -> int:
        return sum(len(t.clients) for t in self.tournees)


def _plage(texte: str) -> tuple[int, int]:
    debut, fin = texte.split("-")
    return en_secondes(debut), en_secondes(fin)


def periodes(reglages: Settings, journee_continue: bool = False) -> list[Periode]:
    if journee_continue:
        return [Periode("journee", en_secondes(reglages.heure_debut_tournee),
                        en_secondes(reglages.heure_fin_tournee), 24 * 3600)]
    return [
        Periode("matin", *_plage(reglages.periode_matin), en_secondes(reglages.bascule_apres_midi)),
        Periode("apres-midi", *_plage(reglages.periode_apres_midi), en_secondes(reglages.bascule_soiree)),
        Periode("soiree", *_plage(reglages.periode_soiree), 24 * 3600),
    ]


def planifier_journee(clients: list[Client], reglages: Settings, max_clients: int,
                      flotte: dict[str, int], journee_continue: bool = False) -> PlanJournee:
    """Segmente (type de camion × période), dimensionne la flotte, puis optimise chaque segment
    dans un budget de temps global inférieur à 10 s."""
    debut = time.perf_counter()
    en_tournee = [vers_client_tournee(c, reglages) for c in clients]
    segments: list[Segment] = segmenter(en_tournee, periodes(reglages, journee_continue))
    dimensionner(segments, max_clients, flotte)
    budgets = repartir_budget([len(s.indices) for s in segments], reglages.optim_budget_total_s)

    plan = PlanJournee()
    for seg, budget in zip(segments, budgets):
        membres = [clients[i] for i in seg.indices]
        if seg.nb_vehicules == 0:                       # aucun véhicule de ce type disponible
            plan.a_reporter.extend(membres)
            continue
        probleme = construire_probleme(
            membres, reglages, max_clients, seg.nb_vehicules,
            depart_s=seg.periode.depart_s, fin_s=seg.periode.fin_s,
            types_vehicules=[seg.type_camion] * seg.nb_vehicules,
        )
        res = optimiser_tournees(probleme, budget)
        for ordre, evaluation in zip(res.tournees, res.evaluations):
            if ordre:
                plan.tournees.append(TourneePlanifiee(
                    seg.type_camion, seg.periode.nom, [membres[k - 1] for k in ordre], evaluation))
        plan.a_reporter.extend(membres[k - 1] for k in res.non_servis)

    plan.duree_calcul_s = round(time.perf_counter() - debut, 2)
    return plan
