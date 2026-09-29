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
