"""Passage des données de la base (clients, zone, véhicules) au problème d'optimisation."""
from app.core.config import Settings
from app.models import Client
from app.models.enums import TypeCamion
from app.optim.matrice import Point, construire_matrice
from app.optim.probleme import ClientTournee, ProblemeTournees, en_secondes


def temps_service_theorique_s(client: Client, reglages: Settings) -> int:
    """Démarrage à froid : valeurs fixes (10 min colis, 20 min palette/hayon).
    Elles seront remplacées par les prédictions du modèle de Machine Learning."""
    minutes = (reglages.temps_service_hayon_min if client.type_camion_requis == TypeCamion.HAYON
               else reglages.temps_service_normal_min)
    return minutes * 60


def construire_probleme(clients: list[Client], reglages: Settings, max_clients: int,
                        nb_vehicules: int = 1) -> ProblemeTournees:
    depart, fin = en_secondes(reglages.heure_debut_tournee), en_secondes(reglages.heure_fin_tournee)
    points = [Point(reglages.depot_latitude, reglages.depot_longitude)]
    points += [Point(float(c.latitude), float(c.longitude)) for c in clients]
    return ProblemeTournees(
        clients=[
            ClientTournee(
                identifiant=c.id_client,
                debut_s=en_secondes(c.creneau_debut) if c.creneau_debut else depart,
                fin_s=en_secondes(c.creneau_fin) if c.creneau_fin else fin,
                temps_service_s=temps_service_theorique_s(c, reglages),
            )
            for c in clients
        ],
        matrice=construire_matrice(points, reglages.coefficient_routier, reglages.vitesse_moyenne_kmh),
        nb_vehicules=nb_vehicules,
        max_clients=max_clients,
        heure_depart_s=depart,
        heure_fin_s=fin,
        penalite_retard_par_seconde=reglages.penalite_retard_par_seconde,
        penalite_client_non_servi=reglages.penalite_client_non_servi,
    )
