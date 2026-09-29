"""Configuration centralisée, lue depuis les variables d'environnement (.env)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "ESIS - Optimisation des tournées"
    environment: str = "dev"

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
    verrouillage_minutes: int = 15
    limite_requetes_auth_par_minute: int = 10
    totp_emetteur: str = "ESIS Tournees"   # nom affiché dans l'application d'authentification
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

    # Géocodage : Nominatim exige un user-agent qui identifie l'application
    nominatim_user_agent: str = "esis-tournees/0.2 (projet etudiant ESIS)"

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
