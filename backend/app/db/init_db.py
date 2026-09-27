"""Création des tables, types ENUM et triggers.

Usage (depuis la racine du projet) :
    docker compose exec api python -m app.db.init_db           # crée ce qui manque
    docker compose exec api python -m app.db.init_db --reset   # DEV : supprime et recrée tout
"""
import sys

from sqlalchemy import inspect, text

import app.models  # noqa: F401  (enregistre tous les modèles dans Base.metadata)
from app.core.config import get_settings
from app.db.base import Base
from app.db.ddl import TOUS_LES_TRIGGERS
from app.db.session import engine


def init_db(reset: bool = False) -> None:
    if reset:
        if get_settings().environment != "dev":
            sys.exit("--reset interdit hors environnement dev")
        print("Suppression de toutes les tables...")
        Base.metadata.drop_all(engine)

    Base.metadata.create_all(engine)  # n'écrase jamais une table existante

    with engine.begin() as conn:
        for script in TOUS_LES_TRIGGERS:
            conn.execute(text(script))

    tables = sorted(inspect(engine).get_table_names())
    print(f"OK - {len(tables)} tables : {', '.join(tables)}")


if __name__ == "__main__":
    init_db(reset="--reset" in sys.argv)
