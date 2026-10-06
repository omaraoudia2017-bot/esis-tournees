"""Configuration centralisée, lue depuis les variables d'environnement (.env)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Road Optim - Optimisation des tournées"
    environment: str = "dev"
    fuseau_horaire: str = "Europe/Paris"   # « tournée du jour » et heures de passage prévues

    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str = "db"
    postgres_port: int = 5432

    fernet_key: str
    jwt_secret_key: str
    jwt_expire_minutes: int = 30

    # Authentification (cahier des charges v6, module 1)
    preauth_expire_minutes: int = 5        # délai pour saisir le code 2FA après le mot de passe
    max_echecs_connexion: int = 5          # verrouillage après 5 échecs
    activation_validite_heures: int = 72   # durée de validité du code d'activation d'un compte
    verrouillage_minutes: int = 15
    limite_requetes_auth_par_minute: int = 10
    totp_emetteur: str = "Road Optim"     # nom affiché dans l'application d'authentification (nouveaux enrôlements)
    nb_codes_secours: int = 10
    cors_origins: list[str] = []           # l'interface Streamlit appelle l'API côté serveur

    # Optimisation des tournées (OR-Tools)
    depot_nom: str = "Depot"
    depot_latitude: float = 45.7578        # à remplacer par l'adresse réelle de l'entrepôt (.env)
    depot_longitude: float = 4.8320
    coefficient_routier: float = 1.3       # distance route ≈ vol d'oiseau × 1,3
    vitesse_moyenne_kmh: float = 30.0      # moyenne urbaine / périurbaine, arrêts compris
    optim_temps_limite_s: int = 5          # critère du cahier des charges : < 10 s pour 20 clients
    retour_depot: bool = True
    heure_debut_tournee: str = "07:00"     # départ du dépôt
    heure_fin_tournee: str = "22:00"       # retour au dépôt au plus tard
    temps_service_normal_min: int = 10     # démarrage à froid (cahier des charges, étape A)
    temps_service_hayon_min: int = 20
    # Périodes « départ-retour » ; un client va dans la période qui contient le milieu de son créneau
    periode_matin: str = "07:00-14:00"
    periode_apres_midi: str = "13:00-19:00"
    periode_soiree: str = "17:00-22:00"
    bascule_apres_midi: str = "13:00"      # milieu de créneau avant 13:00 -> matin
    bascule_soiree: str = "18:00"          # milieu avant 18:00 -> après-midi, sinon soirée
    cout_fixe_vehicule_s: int = 3600       # un camion en plus « coûte » 1 h de trajet
    optim_budget_total_s: float = 9.0      # planification complète < 10 s (marge incluse)
    penalite_retard_par_seconde: int = 10  # 1 min de retard « coûte » 10 min de trajet
    penalite_client_non_servi: int = 1_000_000

    # Apprentissage automatique : modèle de temps de service (dossier monté backend/modeles)
    modele_temps_service: str = "/app/modeles/temps_service.joblib"
    utiliser_modele_temps_service: bool = True   # False = valeurs fixes (comparaison, repli)
    # Détection des connexions anormales (Isolation Forest sur le journal de sécurité)
    modele_anomalies: str = "/app/modeles/anomalies.joblib"
    utiliser_detection_anomalies: bool = True

    # Conditions d'utilisation + information RGPD (fichier app/documents/conditions_utilisation_v<version>.md).
    # Changer la version oblige chaque livreur à relire et accepter le nouveau texte.
    conditions_version: str = "1.1"

    # Journée de trajets (jours sans tournée) : étapes saisies par le livreur
    trajets_max_etapes: int = 15
    # Faisabilité d'une tournée : au plus N clients prévus dans une même heure (8 h - 9 h...) ;
    # la limite d'une tournée = N x heures de sa période (sans dépasser la limite de la zone)
    clients_max_par_heure: int = 2
    trajets_duree_defaut_min: int = 15

    # Écran du livreur : annulation d'un clic Démarrer / Terminer fait par erreur
    delai_annulation_pointage_min: int = 10
    # Remarques sur une adresse : nombre de remarques récentes affichées au livreur
    remarques_affichees: int = 5

    # Géocodage : Nominatim exige un user-agent qui identifie l'application
    nominatim_user_agent: str = "road-optim/1.0 (projet etudiant ESIS)"

    @property
    def database_url(self) -> URL:
        # URL.create échappe correctement les caractères spéciaux du mot de passe
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
