"""Activation d'un nouveau compte : code d'activation remis par le planificateur, choix du mot de
passe (avec confirmation) par le salarié lui-même, puis 2FA (QR code).
Base PostgreSQL réelle, transaction annulée à la fin de chaque test (voir conftest.py)."""
from datetime import datetime, timedelta, timezone

import pyotp
import pytest
from sqlalchemy import select

from app.core.security import verifier_mdp
from app.models import JournalSecurite, Utilisateur
from tests.outils import connecter, entete

MDP_CHOISI = "MonSecretLivreur26"


@pytest.fixture
def admin(client, jeu):
    return entete(connecter(client, "test.admin.j7", jeu["secrets"]["admin"]))


@pytest.fixture
def nouveau(client, admin):
    """Compte créé par le planificateur : (identifiant, code d'activation)."""
    r = client.post("/admin/utilisateurs", headers=admin, json={
        "identifiant": "test.activation", "nom": "Martin", "prenom": "Inès", "type_livreur": "prestataire"})
    assert r.status_code == 201, r.text
    return "test.activation", r.json()["code_activation"]


def _connexion(client, identifiant, secret):
    return client.post("/auth/connexion", json={"identifiant": identifiant, "mot_de_passe": secret})


def _compte(db, identifiant):
    db.expire_all()
    return db.scalar(select(Utilisateur).where(Utilisateur.identifiant == identifiant))


def test_parcours_complet_de_premiere_connexion(client, db, nouveau):
    identifiant, code = nouveau
    # Le code est accepté sans tiret et en minuscules (saisie sur téléphone)
    r = _connexion(client, identifiant, code.replace("-", "").lower())
    assert r.status_code == 200 and r.json()["etape"] == "nouveau_mot_de_passe"
    preauth = entete(r.json()["jeton_preauth"])

    # Tant que le mot de passe n'est pas choisi : pas de 2FA ni de session
    assert client.post("/auth/2fa/enrolement", headers=preauth).status_code == 401
    assert client.get("/auth/moi", headers=preauth).status_code == 401

    r = client.post("/auth/mot-de-passe-initial", json={"nouveau": MDP_CHOISI, "confirmation": MDP_CHOISI},
                    headers=preauth)
    assert r.status_code == 200, r.text
    assert r.json()["etape"] == "enrolement"
    suite = entete(r.json()["jeton_preauth"])
    # Le jeton du code d'activation est à usage unique
    assert client.post("/auth/mot-de-passe-initial", json={"nouveau": MDP_CHOISI, "confirmation": MDP_CHOISI},
                       headers=preauth).status_code == 401

    # QR code puis premier code : session ouverte
    secret = client.post("/auth/2fa/enrolement", headers=suite).json()["cle_manuelle"]
    r = client.post("/auth/2fa/activation", json={"code": pyotp.TOTP(secret).now()}, headers=suite)
    assert r.status_code == 200 and len(r.json()["codes_secours"]) > 0

    u = _compte(db, identifiant)
    assert not u.mdp_a_definir and u.activation_expire_le is None and verifier_mdp(MDP_CHOISI, u.mot_de_passe_hash)
    # Le code ne sert plus ; le mot de passe choisi, si
    assert _connexion(client, identifiant, code).status_code == 401
    assert _connexion(client, identifiant, MDP_CHOISI).json()["etape"] == "verification"
    # Ni le code ni le mot de passe dans le journal
    traces = db.scalars(select(JournalSecurite).where(JournalSecurite.utilisateur_id == u.id)).all()
    evenements = {t.evenement for t in traces}
    assert {"CODE_ACTIVATION_VALIDE", "MOT_DE_PASSE_DEFINI", "2FA_ACTIVEE"} <= evenements
    assert all(code not in str(t.details) and MDP_CHOISI not in str(t.details) for t in traces)


@pytest.mark.parametrize("nouveau_mdp, confirmation, extrait", [
    ("MonSecretLivreur26", "MonSecretLivreur27", "pas identiques"),
    ("court1A", "court1A", "au moins 12 caractères"),
    ("toutenminuscules26", "toutenminuscules26", "majuscules"),
    ("Test.Activation2026", "Test.Activation2026", "identifiant"),
])
def test_mot_de_passe_refuse(client, db, nouveau, nouveau_mdp, confirmation, extrait):
    identifiant, code = nouveau
    preauth = entete(_connexion(client, identifiant, code).json()["jeton_preauth"])
    r = client.post("/auth/mot-de-passe-initial", json={"nouveau": nouveau_mdp, "confirmation": confirmation},
                    headers=preauth)
    assert r.status_code == 422 and extrait in r.json()["detail"]
    assert _compte(db, identifiant).mdp_a_definir                     # rien n'a changé


def test_code_faux_expire_et_verrouillage(client, db, nouveau):
    identifiant, code = nouveau
    # Un identifiant seul ne suffit pas : un code faux reçoit le message générique
    r = _connexion(client, identifiant, "AAAA-AAAA")
    assert r.status_code == 401 and r.json()["detail"] == "Identifiant ou mot de passe incorrect."

    # Code juste mais périmé : message explicite (demander un nouveau code)
    u = _compte(db, identifiant)
    u.activation_expire_le = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    r = _connexion(client, identifiant, code)
    assert r.status_code == 401 and "expiré" in r.json()["detail"]

    # 5 codes faux : compte verrouillé, même avec le bon code
    u.activation_expire_le = datetime.now(timezone.utc) + timedelta(hours=1)
    u.tentatives_echec = 0
    db.commit()
    for _ in range(5):
        _connexion(client, identifiant, "BBBB-BBBB")
    assert _connexion(client, identifiant, code).status_code == 401
    assert _compte(db, identifiant).verrouille_jusqua is not None


def test_jeton_ordinaire_ne_permet_pas_de_changer_le_mot_de_passe(client, db, jeu):
    """Un compte déjà actif ne peut pas passer par la route du premier mot de passe."""
    r = _connexion(client, "test.livreur.a", "MotDePasseTest2026")
    assert r.json()["etape"] == "verification"
    r = client.post("/auth/mot-de-passe-initial", json={"nouveau": MDP_CHOISI, "confirmation": MDP_CHOISI},
                    headers=entete(r.json()["jeton_preauth"]))
    assert r.status_code == 401


def test_planificateur_voit_l_activation_en_attente(client, db, admin, nouveau):
    identifiant, _ = nouveau
    c = next(x for x in client.get("/admin/utilisateurs", headers=admin).json() if x["identifiant"] == identifiant)
    assert c["activation_en_attente"] and c["activation_expire_le"]
    # Un nouveau code (réinitialisation) remplace l'ancien
    _, ancien = nouveau
    u = _compte(db, identifiant)
    r = client.post(f"/admin/utilisateurs/{u.id}/reinitialiser-mdp", headers=admin)
    assert r.status_code == 200 and r.json()["code_activation"] != ancien
    assert _connexion(client, identifiant, ancien).status_code == 401
    assert _connexion(client, identifiant, r.json()["code_activation"]).json()["etape"] == "nouveau_mot_de_passe"
