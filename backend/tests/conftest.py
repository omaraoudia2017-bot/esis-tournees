"""Fixtures partagées des tests d'API, sur la vraie base PostgreSQL.

Chaque test tourne dans une transaction annulée à la fin : la base n'est jamais modifiée.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import limiteur_auth, limiteur_ip
from app.db.session import engine, get_db
from app.main import app


@pytest.fixture
def db():
    connexion = engine.connect()
    transaction = connexion.begin()
    session = Session(bind=connexion, join_transaction_mode="create_savepoint", expire_on_commit=False)
    app.dependency_overrides[get_db] = lambda: session
    limiteur_auth.reinitialiser()
    limiteur_ip.reinitialiser()
    yield session
    app.dependency_overrides.clear()
    session.close()
    transaction.rollback()
    connexion.close()


@pytest.fixture
def client(db):
    return TestClient(app)
