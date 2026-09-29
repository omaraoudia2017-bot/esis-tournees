"""Fixtures partagées des tests d'API, sur la vraie base PostgreSQL.

Chaque test tourne dans une transaction annulée à la fin : la base n'est jamais modifiée.
"""
from datetime import time
from decimal import Decimal

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import limiteur_auth, limiteur_ip
from app.core.config import get_settings
from app.db.session import engine, get_db
from app.main import app
from app.models import Client, Livraison, Tournee, Vehicule, Zone
from app.models.enums import RoleUtilisateur, SourceClient, TypeCamion
from app.services.tournees import aujourdhui
from tests.outils import creer_compte


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


# (latitude, longitude, créneau, type de camion) — 4 clients normaux dans Lyon + 1 client hayon
CLIENTS = [
    ("45.7640", "4.8357", (8, 10), TypeCamion.NORMAL),
    ("45.7485", "4.8467", (8, 12), TypeCamion.NORMAL),
    ("45.7719", "4.8902", (10, 12), TypeCamion.NORMAL),
    ("45.7578", "4.8320", (9, 11), TypeCamion.NORMAL),
    ("45.7400", "4.8700", (8, 12), TypeCamion.HAYON),
]


@pytest.fixture(autouse=True)
def calcul_rapide(monkeypatch):
    monkeypatch.setattr(get_settings(), "optim_temps_limite_s", 1)


@pytest.fixture
def jeu(db):
    """Une zone, un camion normal, deux livreurs (A a une tournée, B n'en a pas), un admin."""
    zone = Zone(nom="Zone de test J7", max_clients=10)
    vehicule = Vehicule(immatriculation="TEST-J7-N1", type_camion=TypeCamion.NORMAL)
    db.add_all([zone, vehicule])
    db.flush()
    secrets = {nom: pyotp.random_base32() for nom in ("a", "b", "admin")}
    livreur_a = creer_compte(db, "test.livreur.a", secret=secrets["a"], vehicule_id=vehicule.id)
    creer_compte(db, "test.livreur.b", secret=secrets["b"], vehicule_id=vehicule.id)
    creer_compte(db, "test.admin.j7", RoleUtilisateur.ADMIN, secrets["admin"])

    tournee = Tournee(date_tournee=aujourdhui(get_settings()), livreur_id=livreur_a.id,
                      vehicule_id=vehicule.id, zone_id=zone.id, max_clients=10,
                      heure_depart=time(7), heure_fin=time(14))
    db.add(tournee)
    db.flush()
    for i, (lat, lon, (h1, h2), type_camion) in enumerate(CLIENTS, start=1):
        identifiant = f"88990000{i:02d}"
        db.add(Client(id_client=identifiant, nom_client=f"Client test {i}", adresse=f"{i} rue du Test",
                      code_postal="69003", ville="LYON", latitude=Decimal(lat), longitude=Decimal(lon),
                      creneau_debut=time(h1), creneau_fin=time(h2), type_camion_requis=type_camion,
                      digicode=f"{i}234A", source=SourceClient.SIMULATION))
        db.flush()
        db.add(Livraison(tournee_id=tournee.id, client_id=identifiant))
    db.commit()
    return {"tournee": tournee, "secrets": secrets}
