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
