"""Conditions d'utilisation et information RGPD : acceptation obligatoire du livreur avant tout accès,
versionnée, datée et tracée. Base PostgreSQL réelle, transaction annulée (voir conftest.py)."""
import pyotp
from sqlalchemy import select

from app.core.config import get_settings
from app.models import JournalSecurite
from app.models.enums import RoleUtilisateur
from tests.outils import connecter, creer_compte, entete

ACCEPTATION = {"conditions_acceptees": True, "information_lue": True}


def _nouveau_livreur(db, client, identifiant="test.cgu"):
    secret = pyotp.random_base32()
    u = creer_compte(db, identifiant, secret=secret, conditions_acceptees=False)
    return u, entete(connecter(client, identifiant, secret))


def test_livreur_bloque_tant_que_non_acceptees(client, db, jeu):
    _, h = _nouveau_livreur(db, client)
    moi = client.get("/auth/moi", headers=h).json()
    assert moi["conditions_a_accepter"] is True and moi["conditions_version"] is None

    # Toutes les routes du livreur sont fermées, quel que soit l'écran (contrôle côté API)
    for methode, chemin in [("GET", "/tournees/du-jour"), ("POST", "/tournees/du-jour/optimiser"),
                            ("POST", "/livraisons/1/demarrer"), ("POST", "/tournees/du-jour/annuler-dernier-pointage")]:
        r = client.request(methode, chemin, headers=h)
        assert r.status_code == 403 and "conditions" in r.json()["detail"], chemin

    # Le texte reste lisible, avec l'information RGPD
    r = client.get("/conditions", headers=h)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    texte = r.json()["texte"]
    assert r.json()["a_accepter"] is True and r.json()["version"] == get_settings().conditions_version
    for mention in ("Responsable du traitement", "Base légale", "CNIL", "ne sert pas à évaluer"):
        assert mention in texte


def test_acceptation_puis_acces(client, db, jeu):
    u, h = _nouveau_livreur(db, client)
    version = get_settings().conditions_version
    r = client.post("/conditions/accepter", json={"version": version, **ACCEPTATION}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["a_accepter"] is False and r.json()["version_acceptee"] == version
    assert r.json()["acceptee_le"] is not None

    assert client.get("/auth/moi", headers=h).json()["conditions_a_accepter"] is False
    assert client.get("/tournees/du-jour", headers=h).status_code == 404     # accès ouvert (pas de tournée)

    trace = db.scalar(select(JournalSecurite).where(JournalSecurite.evenement == "CONDITIONS_ACCEPTEES",
                                                    JournalSecurite.utilisateur_id == u.id))
    assert trace is not None and trace.details["version"] == version


def test_acceptation_refusee_si_case_manquante_ou_mauvaise_version(client, db, jeu):
    _, h = _nouveau_livreur(db, client)
    version = get_settings().conditions_version
    for demande in ({"version": version, "conditions_acceptees": True, "information_lue": False},
                    {"version": version, "conditions_acceptees": False, "information_lue": True},
                    {"version": version}):
        assert client.post("/conditions/accepter", json=demande, headers=h).status_code == 422
    r = client.post("/conditions/accepter", json={"version": "0.9", **ACCEPTATION}, headers=h)
    assert r.status_code == 409
    assert client.get("/tournees/du-jour", headers=h).status_code == 403


def test_nouvelle_version_a_relire(client, db, jeu, monkeypatch):
    jeton = connecter(client, "test.livreur.a", jeu["secrets"]["a"])        # a accepté la v en vigueur
    assert client.get("/tournees/du-jour", headers=entete(jeton)).status_code == 200
    monkeypatch.setattr(get_settings(), "conditions_version", "9.9")
    r = client.get("/tournees/du-jour", headers=entete(jeton))
    assert r.status_code == 403 and "conditions" in r.json()["detail"]
    assert client.get("/auth/moi", headers=entete(jeton)).json()["conditions_a_accepter"] is True


def test_administrateur_non_concerne_et_suivi_des_acceptations(client, db, jeu):
    secret = pyotp.random_base32()
    creer_compte(db, "test.admin.cgu", RoleUtilisateur.ADMIN, secret, conditions_acceptees=False)
    h_admin = entete(connecter(client, "test.admin.cgu", secret))
    comptes = client.get("/admin/utilisateurs", headers=h_admin)
    assert comptes.status_code == 200
    a = next(c for c in comptes.json() if c["identifiant"] == "test.livreur.a")
    assert a["conditions_version"] == get_settings().conditions_version and a["conditions_acceptees_le"]
