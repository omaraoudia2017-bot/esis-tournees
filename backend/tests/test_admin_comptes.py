"""Espace administrateur : contrôle d'accès et gestion des comptes (module 4).

Fixtures `db`, `client`, `jeu` : voir conftest.py (livreurs test.livreur.a / b, admin test.admin.j7).
Les jetons « déjà ouverts » sont fabriqués directement : une 2e connexion TOTP dans la même
fenêtre de 30 s serait refusée par l'anti-rejeu.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.security import TYPE_ACCES, creer_jeton, valider_politique_mdp
from app.models import CodeSecours, JournalSecurite, Utilisateur
from tests.outils import MDP, connecter, entete


@pytest.fixture
def admin(client, jeu):
    return entete(connecter(client, "test.admin.j7", jeu["secrets"]["admin"]))


def compte(db, identifiant) -> Utilisateur:
    return db.scalar(select(Utilisateur).where(Utilisateur.identifiant == identifiant))


def session_ouverte(u: Utilisateur) -> dict:
    jeton, _, _ = creer_jeton(u.id, u.role.value, TYPE_ACCES, timedelta(minutes=30))
    return entete(jeton)


def journal(db, evenement):
    return db.scalars(select(JournalSecurite).where(JournalSecurite.evenement == evenement)
                      .order_by(JournalSecurite.id.desc())).first()


ROUTES_ADMIN = [("GET", "/admin/utilisateurs", None), ("GET", "/admin/tableau-de-bord", None),
                ("GET", "/admin/clients", None), ("GET", "/admin/clients/8899000001/export", None),
                ("POST", "/admin/import-csv", {"nom_fichier": "x.csv", "contenu": "a"}),
                ("POST", "/admin/tournees", {"livreur_id": 1, "clients": ["8899000001"]}),
                ("POST", "/admin/utilisateurs/1/desactiver", None)]


def test_un_livreur_n_accede_a_aucune_route_admin(client, db, jeu):
    livreur = entete(connecter(client, "test.livreur.a", jeu["secrets"]["a"]))
    for methode, route, corps in ROUTES_ADMIN:
        assert client.request(methode, route, json=corps, headers=livreur).status_code == 403, route
        assert client.request(methode, route, json=corps).status_code == 401, route   # sans jeton
    refus = journal(db, "ACCES_REFUSE")
    assert refus.identifiant_saisi == "test.livreur.a" and refus.details["route"].startswith("/admin")


def test_creation_d_un_livreur(client, db, jeu, admin):
    vehicule_id = compte(db, "test.livreur.a").vehicule_id
    r = client.post("/admin/utilisateurs", headers=admin, json={
        "identifiant": "test.nouveau", "nom": "durand", "prenom": "Léa", "vehicule_id": vehicule_id})
    assert r.status_code == 201, r.text
    corps = r.json()
    mdp = corps["mot_de_passe_temporaire"]
    assert valider_politique_mdp(mdp) == [] and corps["compte"]["nom"] == "DURAND"
    assert corps["compte"]["vehicule"] == "TEST-J7-N1" and not corps["compte"]["totp_active"]
    assert mdp not in str(journal(db, "ADMIN_CREATION_COMPTE").details)    # jamais au journal
    # Première connexion : la 2FA est imposée (enrôlement)
    r = client.post("/auth/connexion", json={"identifiant": "test.nouveau", "mot_de_passe": mdp})
    assert r.status_code == 200 and r.json()["etape"] == "enrolement"

    assert client.post("/admin/utilisateurs", headers=admin, json={
        "identifiant": "test.nouveau", "nom": "X", "prenom": "Y"}).status_code == 409
    assert client.post("/admin/utilisateurs", headers=admin, json={
        "identifiant": "Pas Valide!", "nom": "X", "prenom": "Y"}).status_code == 422
    assert client.post("/admin/utilisateurs", headers=admin, json={
        "identifiant": "test.vehicule.fantome", "nom": "X", "prenom": "Y",
        "vehicule_id": 999999}).status_code == 422


def test_desactivation_coupe_la_session_immediatement(client, db, jeu, admin):
    livreur = compte(db, "test.livreur.a")
    session = session_ouverte(livreur)
    assert client.get("/tournees/du-jour", headers=session).status_code == 200
    r = client.post(f"/admin/utilisateurs/{livreur.id}/desactiver", headers=admin)
    assert r.status_code == 200 and not r.json()["compte"]["actif"]
    assert client.get("/tournees/du-jour", headers=session).status_code == 401
    assert client.post("/auth/connexion", json={"identifiant": "test.livreur.a",
                                                "mot_de_passe": MDP}).status_code == 401
    assert journal(db, "ADMIN_DESACTIVER").details["cible"] == "test.livreur.a"

    assert client.post(f"/admin/utilisateurs/{livreur.id}/reactiver", headers=admin).json()["compte"]["actif"]
    # Un administrateur ne peut pas se désactiver lui-même
    moi = compte(db, "test.admin.j7")
    assert client.post(f"/admin/utilisateurs/{moi.id}/desactiver", headers=admin).status_code == 409
    assert client.post(f"/admin/utilisateurs/{livreur.id}/supprimer", headers=admin).status_code == 422
    assert client.post("/admin/utilisateurs/999999/desactiver", headers=admin).status_code == 404


def test_reinitialisation_du_mot_de_passe(client, db, jeu, admin):
    livreur = compte(db, "test.livreur.b")
    ancienne_session = session_ouverte(livreur)
    r = client.post(f"/admin/utilisateurs/{livreur.id}/reinitialiser-mdp", headers=admin)
    assert r.status_code == 200
    nouveau = r.json()["mot_de_passe_temporaire"]
    assert valider_politique_mdp(nouveau) == []
    assert client.get("/auth/moi", headers=ancienne_session).status_code == 401      # session coupée
    assert client.post("/auth/connexion", json={"identifiant": "test.livreur.b",
                                                "mot_de_passe": MDP}).status_code == 401
    r = client.post("/auth/connexion", json={"identifiant": "test.livreur.b", "mot_de_passe": nouveau})
    assert r.status_code == 200 and r.json()["etape"] == "verification"      # 2FA conservée
    assert client.get("/auth/moi", headers=session_ouverte(livreur)).status_code == 200   # nouvelle session OK


def test_reinitialisation_2fa_et_deverrouillage(client, db, jeu, admin):
    livreur = compte(db, "test.livreur.b")
    db.add(CodeSecours(utilisateur_id=livreur.id, empreinte="x" * 64))
    livreur.verrouille_jusqua = datetime.now(timezone.utc) + timedelta(minutes=10)
    db.commit()
    comptes = {c["identifiant"]: c for c in client.get("/admin/utilisateurs", headers=admin).json()}
    assert comptes["test.livreur.b"]["verrouille"] and comptes["test.livreur.b"]["totp_active"]

    r = client.post(f"/admin/utilisateurs/{livreur.id}/deverrouiller", headers=admin)
    assert r.status_code == 200 and not r.json()["compte"]["verrouille"]
    r = client.post(f"/admin/utilisateurs/{livreur.id}/reinitialiser-2fa", headers=admin)
    assert r.status_code == 200 and not r.json()["compte"]["totp_active"]
    assert r.json()["mot_de_passe_temporaire"] is None
    assert not db.scalars(select(CodeSecours).where(CodeSecours.utilisateur_id == livreur.id)).all()
    r = client.post("/auth/connexion", json={"identifiant": "test.livreur.b", "mot_de_passe": MDP})
    assert r.json()["etape"] == "enrolement"                  # nouveau QR code à scanner


def test_affectation_du_vehicule(client, db, jeu, admin):
    vehicules = client.get("/admin/vehicules", headers=admin).json()
    test = next(v for v in vehicules if v["immatriculation"] == "TEST-J7-N1")
    assert test["utilise_par"] in ("test.livreur.a", "test.livreur.b")
    livreur, moi = compte(db, "test.livreur.b"), compte(db, "test.admin.j7")
    r = client.post(f"/admin/utilisateurs/{livreur.id}/vehicule", json={"vehicule_id": None}, headers=admin)
    assert r.status_code == 200 and r.json()["vehicule"] is None
    assert client.post(f"/admin/utilisateurs/{moi.id}/vehicule", json={"vehicule_id": test["id"]},
                       headers=admin).status_code == 422          # pas de camion pour un admin
    assert journal(db, "ADMIN_AFFECTATION_VEHICULE").details["cible"] == "test.livreur.b"
