"""Passage des données de la base (clients, zone, flotte) aux problèmes d'optimisation."""
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import Settings
from app.ml.temps_service import PredicteurTempsService
from app.ml.variables import variables_client
from app.models import Client
from app.models.enums import TypeCamion
from app.optim.cvrptw import optimiser_tournees
from app.optim.faisabilite import depart_au_plus_tard_s, placer_par_heure
from app.optim.matrice import Point, construire_matrice, construire_matrice_depart
from app.optim.probleme import ClientTournee, ParcoursEvalue, ProblemeTournees, en_secondes
from app.optim.segmentation import Periode, Segment, dimensionner, repartir_budget, segmenter

journal_technique = logging.getLogger(__name__)   # erreurs techniques (logs du conteneur)


def temps_service_theorique_s(client: Client, reglages: Settings) -> int:
    """Démarrage à froid : valeurs fixes (10 min colis, 20 min palette/hayon)."""
    minutes = (reglages.temps_service_hayon_min if client.type_camion_requis == TypeCamion.HAYON
               else reglages.temps_service_normal_min)
    return minutes * 60


_predicteurs: dict[tuple[str, float], PredicteurTempsService] = {}


def predicteur(reglages: Settings) -> PredicteurTempsService | None:
    """Modèle de temps de service s'il est activé et entraîné. Rechargé automatiquement
    quand le fichier change (nouvel entraînement), sans redémarrer l'API."""
    if not reglages.utiliser_modele_temps_service:
        return None
    chemin = Path(reglages.modele_temps_service)
    try:
        cle = (str(chemin), chemin.stat().st_mtime)
    except OSError:
        return None
    if cle not in _predicteurs:
        _predicteurs.clear()
        try:
            _predicteurs[cle] = PredicteurTempsService(chemin)
        except Exception:      # fichier corrompu : valeurs fixes plutôt qu'une panne
            _predicteurs[cle] = PredicteurTempsService(None)
    p = _predicteurs[cle]
    return p if p.actif else None


def estimer_temps_service_s(clients: list[Client], reglages: Settings) -> tuple[list[int], str]:
    """Temps de service de chaque client, en secondes, et leur origine : "modele" (Random Forest)
    ou "fixe" (démarrage à froid, ou modèle absent / illisible)."""
    p = predicteur(reglages)
    if p is not None and clients:
        depot = (reglages.depot_latitude, reglages.depot_longitude)
        try:
            return p.predire_s([variables_client(c, depot) for c in clients]), "modele"
        except Exception:      # modèle incompatible : l'optimisation doit toujours fonctionner
            journal_technique.warning("Modèle de temps de service inutilisable : valeurs fixes", exc_info=True)
    return [temps_service_theorique_s(c, reglages) for c in clients], "fixe"


def vers_client_tournee(c: Client, reglages: Settings, temps_service_s: int | None = None) -> ClientTournee:
    return ClientTournee(
        identifiant=c.id_client,
        debut_s=en_secondes(c.creneau_debut) if c.creneau_debut else en_secondes(reglages.heure_debut_tournee),
        fin_s=en_secondes(c.creneau_fin) if c.creneau_fin else en_secondes(reglages.heure_fin_tournee),
        temps_service_s=temps_service_theorique_s(c, reglages) if temps_service_s is None else temps_service_s,
        type_requis=c.type_camion_requis.value,
    )


def construire_probleme(clients: list[Client], reglages: Settings, max_clients: int,
                        nb_vehicules: int = 1, depart_s: int | None = None, fin_s: int | None = None,
                        types_vehicules: list[str] | None = None,
                        position: Point | None = None,
                        temps_service_s: list[int] | None = None,
                        arrivee: Point | None = None) -> ProblemeTournees:
    """`position` : point de départ du livreur en cours de tournée (sinon le dépôt).
    `arrivee` : fin de tournée (domicile du livreur interne), sinon retour au dépôt.
    `temps_service_s` : par défaut, estimé par le modèle."""
    depot = Point(reglages.depot_latitude, reglages.depot_longitude)
    points = [Point(float(c.latitude), float(c.longitude)) for c in clients]
    matrice = construire_matrice_depart(position or depot, depot, points, reglages.coefficient_routier,
                                        reglages.vitesse_moyenne_kmh, arrivee=arrivee)
    if temps_service_s is None:
        temps_service_s, _ = estimer_temps_service_s(clients, reglages)
    return ProblemeTournees(
        clients=[vers_client_tournee(c, reglages, t) for c, t in zip(clients, temps_service_s)],
        matrice=matrice,
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


# --------------------------------------------------------------------------- faisabilité (avant optimisation)
@dataclass
class Faisabilite:
    heures: dict[str, int]                 # identifiant client -> heure visée (8 = 8 h - 9 h)
    refuses: list[str]                     # clients impossibles à placer (trop de clients sur leur créneau)
    ordre: list[str]                       # ordre horaire proposé (heure visée, début, fin de créneau)
    depart_au_plus_tard_s: int | None      # heure limite de départ du dépôt, dans cet ordre


def faisabilite(clients: list[Client], periode: Periode, reglages: Settings,
                temps_service_s: list[int] | None = None, par_heure: int | None = None) -> Faisabilite:
    """Placement « N clients au plus par heure », ordre horaire et départ au plus tard du dépôt
    (calculé dans l'ordre horaire : l'optimisation géographique ne peut que faire mieux ou pareil)."""
    if not clients:
        return Faisabilite({}, [], [], None)
    temps = temps_service_s or [None] * len(clients)
    en_tournee = [vers_client_tournee(c, reglages, t) for c, t in zip(clients, temps)]
    places, refuses = placer_par_heure(en_tournee, periode, par_heure or reglages.clients_max_par_heure)
    ordre = sorted(places, key=lambda i: (places[i], en_tournee[i].debut_s, en_tournee[i].fin_s))
    depart = None
    if ordre:
        depot = Point(reglages.depot_latitude, reglages.depot_longitude)
        points = [Point(float(clients[i].latitude), float(clients[i].longitude)) for i in ordre]
        matrice = construire_matrice([depot, *points], reglages.coefficient_routier, reglages.vitesse_moyenne_kmh)
        depart = depart_au_plus_tard_s(matrice, [en_tournee[i] for i in ordre])
    return Faisabilite({clients[i].id_client: h for i, h in places.items()},
                       [clients[i].id_client for i in refuses],
                       [clients[i].id_client for i in ordre], depart)
