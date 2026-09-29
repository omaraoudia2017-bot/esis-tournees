"""Tests de bout en bout de l'authentification, sur la vraie base PostgreSQL.

Chaque test tourne dans une transaction annulée à la fin : la base n'est jamais modifiée.
"""
from urllib.parse import parse_qs, urlparse

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import limiteur_auth, limiteur_ip
from app.core.security import hacher_mdp
from app.db.session import engine, get_db
from app.main import app
from app.models import JournalSecurite, Utilisateur
from app.models.enums import RoleUtilisateur

MDP = "MotDePasseTest2026"


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


def creer_compte(db, identifiant, role=RoleUtilisateur.LIVREUR, secret=None) -> Utilisateur:
    u = Utilisateur(identifiant=identifiant, nom="TEST", prenom="Test", role=role,
                    mot_de_passe_hash=hacher_mdp(MDP), totp_secret=secret, totp_active=bool(secret))
    db.add(u)
    db.commit()
    return u


def connecter(client, identifiant, secret) -> str:
    r = client.post("/auth/connexion", json={"identifiant": identifiant, "mot_de_passe": MDP})
    assert r.status_code == 200 and r.json()["etape"] == "verification"
    preauth = {"Authorization": f"Bearer {r.json()['jeton_preauth']}"}
    r = client.post("/auth/2fa/verification", json={"code": pyotp.TOTP(secret).now()}, headers=preauth)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def entete(jeton):
    return {"Authorization": f"Bearer {jeton}"}


def test_premiere_connexion_enrolement_puis_codes_de_secours(client, db):
    creer_compte(db, "test.enrolement")
    r = client.post("/auth/connexion", json={"identifiant": "test.enrolement", "mot_de_passe": MDP})
    assert r.status_code == 200 and r.json()["etape"] == "enrolement"
    preauth = entete(r.json()["jeton_preauth"])

    r = client.post("/auth/2fa/enrolement", headers=preauth)
    assert r.status_code == 200 and r.json()["qr_code_png_base64"]
    secret = parse_qs(urlparse(r.json()["uri_otpauth"]).query)["secret"][0]

    code = pyotp.TOTP(secret).now()
    r = client.post("/auth/2fa/activation", json={"code": code}, headers=preauth)
    assert r.status_code == 200, r.text
    codes = r.json()["codes_secours"]
    assert len(codes) == 10
    assert client.get("/auth/moi", headers=entete(r.json()["access_token"])).json()["totp_active"]

    # Le jeton de pré-authentification est à usage unique
    assert client.post("/auth/2fa/verification", json={"code": code}, headers=preauth).status_code == 401

    # Nouvelle connexion : le même code TOTP (même fenêtre de 30 s) est refusé -> rejeu impossible
    r = client.post("/auth/connexion", json={"identifiant": "test.enrolement", "mot_de_passe": MDP})
    preauth = entete(r.json()["jeton_preauth"])
    assert client.post("/auth/2fa/verification", json={"code": code}, headers=preauth).status_code == 401

    # Un code de secours fonctionne une seule fois
    assert client.post("/auth/2fa/verification", json={"code": codes[0]}, headers=preauth).status_code == 200
    r = client.post("/auth/connexion", json={"identifiant": "test.enrolement", "mot_de_passe": MDP})
    preauth = entete(r.json()["jeton_preauth"])
    assert client.post("/auth/2fa/verification", json={"code": codes[0]}, headers=preauth).status_code == 401


def test_message_generique(client, db):
    creer_compte(db, "test.generique")
    inconnu = client.post("/auth/connexion", json={"identifiant": "personne", "mot_de_passe": MDP})
    mauvais = client.post("/auth/connexion", json={"identifiant": "test.generique", "mot_de_passe": "Faux2026Faux"})
    assert inconnu.status_code == mauvais.status_code == 401
    assert inconnu.json() == mauvais.json()          # rien ne révèle que le compte existe


def test_verrouillage_apres_5_echecs(client, db):
    u = creer_compte(db, "test.verrou", secret=pyotp.random_base32())
    for _ in range(5):
        client.post("/auth/connexion", json={"identifiant": "test.verrou", "mot_de_passe": "Faux2026Faux"})
    # Même avec le bon mot de passe, le compte est bloqué
    r = client.post("/auth/connexion", json={"identifiant": "test.verrou", "mot_de_passe": MDP})
    assert r.status_code == 401
    db.refresh(u)
    assert u.verrouille_jusqua is not None
    evenements = db.scalars(select(JournalSecurite.evenement)
                            .where(JournalSecurite.utilisateur_id == u.id)).all()
    assert evenements.count("CONNEXION_ECHEC") == 6 and "COMPTE_VERROUILLE" in evenements


def test_roles_et_deconnexion(client, db):
    s_livreur, s_admin = pyotp.random_base32(), pyotp.random_base32()
    creer_compte(db, "test.livreur", RoleUtilisateur.LIVREUR, s_livreur)
    creer_compte(db, "test.admin", RoleUtilisateur.ADMIN, s_admin)
    jeton_livreur = connecter(client, "test.livreur", s_livreur)
    jeton_admin = connecter(client, "test.admin", s_admin)

    assert client.get("/admin/journal-securite", headers=entete(jeton_livreur)).status_code == 403
    assert client.get("/admin/journal-securite", headers=entete(jeton_admin)).status_code == 200
    assert client.get("/admin/journal-securite").status_code == 401

    assert client.post("/auth/deconnexion", headers=entete(jeton_livreur)).status_code == 204
    assert client.get("/auth/moi", headers=entete(jeton_livreur)).status_code == 401   # jeton révoqué


def test_jeton_preauth_ne_donne_pas_acces(client, db):
    creer_compte(db, "test.preauth", secret=pyotp.random_base32())
    r = client.post("/auth/connexion", json={"identifiant": "test.preauth", "mot_de_passe": MDP})
    assert client.get("/auth/moi", headers=entete(r.json()["jeton_preauth"])).status_code == 401


def test_limitation_de_debit(client, db):
    codes = [client.post("/auth/connexion", json={"identifiant": "robot", "mot_de_passe": "x"}).status_code
             for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429
