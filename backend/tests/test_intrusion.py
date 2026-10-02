"""Tests de sécurité « offensifs » (Jour 13) : tentatives d'accès non autorisé, falsification de
jeton, injections, assignation de masse, données au repos, intégrité du journal, en-têtes HTTP.

Chaque test simule ce qu'un attaquant essaierait, et vérifie que l'application refuse proprement.
Fixtures `db`, `client`, `jeu` : voir conftest.py.
"""
import base64
import json
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.api.routes_tournees import geocodeur
from app.core.security import TYPE_ACCES, creer_jeton
from app.main import app
from app.models import Client, JournalSecurite, Utilisateur
from app.models.enums import PrecisionGeocodage
from app.services.geocodage import ResultatGeocodage
from tests.outils import MDP, connecter, entete
from tests.test_ajout_volee import NOUVEAU, FauxGeocodeur


def compte(db, identifiant) -> Utilisateur:
    return db.scalar(select(Utilisateur).where(Utilisateur.identifiant == identifiant))


def charge(sujet: int, role: str) -> dict:
    maintenant = datetime.now(timezone.utc)
    return {"sub": str(sujet), "role": role, "typ": TYPE_ACCES, "jti": str(uuid.uuid4()),
            "iat": int(maintenant.timestamp()), "emis": maintenant.timestamp(),
            "exp": int((maintenant + timedelta(minutes=30)).timestamp())}


def b64(donnees: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(donnees).encode()).rstrip(b"=").decode()


# --------------------------------------------------------------------------- jetons
def test_jeton_signe_avec_une_autre_cle_est_refuse(client, db, jeu):
    livreur = compte(db, "test.livreur.a")
    faux = jwt.encode(charge(livreur.id, "admin"), "cle-devinee-par-l-attaquant-" + "x" * 32,
                      algorithm="HS256")
    assert client.get("/tournees/du-jour", headers=entete(faux)).status_code == 401
    assert client.get("/admin/utilisateurs", headers=entete(faux)).status_code == 401


def test_jeton_sans_signature_alg_none_est_refuse(client, db, jeu):
    admin = compte(db, "test.admin.j7")
    sans_signature = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(charge(admin.id, 'admin'))}."
    assert client.get("/admin/utilisateurs", headers=entete(sans_signature)).status_code == 401


def test_le_role_ecrit_dans_le_jeton_ne_donne_aucun_droit(client, db, jeu):
    """Jeton authentique d'un livreur, mais qui prétend être « admin » : le rôle est relu en base."""
    livreur = compte(db, "test.livreur.a")
    jeton, _, _ = creer_jeton(livreur.id, "admin", TYPE_ACCES, timedelta(minutes=30))
    assert client.get("/admin/utilisateurs", headers=entete(jeton)).status_code == 403
    refus = db.scalars(select(JournalSecurite).where(JournalSecurite.evenement == "ACCES_REFUSE")
                       .order_by(JournalSecurite.id.desc())).first()
    assert refus.identifiant_saisi == "test.livreur.a"


def test_jeton_d_un_compte_inexistant_ou_expire(client, db, jeu):
    jeton, _, _ = creer_jeton(2_000_000_000, "admin", TYPE_ACCES, timedelta(minutes=30))
    assert client.get("/admin/utilisateurs", headers=entete(jeton)).status_code == 401
    admin = compte(db, "test.admin.j7")
    expire, _, _ = creer_jeton(admin.id, "admin", TYPE_ACCES, timedelta(seconds=-5))
    assert client.get("/admin/utilisateurs", headers=entete(expire)).status_code == 401
    for entete_invalide in ("Bearer", "Bearer abc.def.ghi", "Basic YWRtaW46YWRtaW4="):
        assert client.get("/admin/utilisateurs",
                          headers={"Authorization": entete_invalide}).status_code == 401


# --------------------------------------------------------------------------- injections
def test_injection_sql_sans_effet(client, db, jeu):
    for identifiant in ("admin.omar' OR '1'='1", "' OR 1=1 --", "test.admin.j7'; DROP TABLE clients; --"):
        r = client.post("/auth/connexion", json={"identifiant": identifiant, "mot_de_passe": MDP})
        assert r.status_code == 401                         # même réponse qu'un compte inconnu
    admin = entete(connecter(client, "test.admin.j7", jeu["secrets"]["admin"]))
    r = client.get("/admin/clients", headers=admin, params={"recherche": "%' OR '1'='1' --"})
    assert r.status_code == 200 and r.json() == []          # le texte est cherché tel quel
    assert client.get("/admin/clients/1' OR '1'='1/export", headers=admin).status_code in (404, 422)
    assert db.scalar(text("SELECT count(*) FROM clients")) > 0                   # table intacte


def test_assignation_de_masse_ignoree(client, db, jeu):
    """Un livreur ajoute un client en glissant des champs non prévus (identifiant d'un client
    existant, source, coordonnées) : ils sont ignorés, il ne peut pas lire un autre client."""
    app.dependency_overrides[geocodeur] = lambda: FauxGeocodeur(
        ResultatGeocodage(45.7605, 4.8490, PrecisionGeocodage.ADRESSE))
    jeton = connecter(client, "test.livreur.a", jeu["secrets"]["a"])
    r = client.post("/tournees/du-jour/clients", headers=entete(jeton), json={
        **NOUVEAU, "id_client": "8899000002", "source": "import_csv", "latitude": 0, "role": "admin"})
    assert r.status_code == 201, r.text
    cree = r.json()["livraison"]["client"]
    assert cree["id_client"].startswith("7") and cree["latitude"] == 45.7605
    assert db.get(Client, cree["id_client"]).source.value == "saisie_manuelle"


def test_charges_trop_volumineuses_refusees(client, db, jeu):
    admin = entete(connecter(client, "test.admin.j7", jeu["secrets"]["admin"]))
    r = client.post("/admin/import-csv", headers=admin,
                    json={"nom_fichier": "gros.csv", "contenu": "x" * 2_000_001})
    assert r.status_code == 422
    r = client.post("/auth/connexion", json={"identifiant": "a" * 51, "mot_de_passe": "b" * 129})
    assert r.status_code == 422


# --------------------------------------------------------------------------- données au repos
def test_donnees_sensibles_chiffrees_en_base(client, db, jeu):
    """Ce que verrait quelqu'un qui lirait directement la base (copie volée, accès psql)."""
    brut = db.execute(text("SELECT digicode FROM clients WHERE id_client = '8899000001'")).scalar_one()
    assert brut != "1234A" and brut.startswith("gAAAAA")            # jeton Fernet
    assert db.get(Client, "8899000001").digicode == "1234A"          # lisible par l'application
    secret, empreinte = db.execute(text(
        "SELECT totp_secret, mot_de_passe_hash FROM utilisateurs WHERE identifiant = 'test.livreur.a'")).one()
    assert secret.startswith("gAAAAA") and jeu["secrets"]["a"] not in secret
    assert empreinte.startswith("$2b$12$") and MDP not in empreinte  # bcrypt, coût 12


def test_journal_de_securite_inalterable(db, jeu):
    """Même avec un accès SQL direct, une trace récente ne peut être ni modifiée ni effacée."""
    db.execute(text("INSERT INTO journal_securite (evenement, succes) VALUES ('TEST_INTEGRITE', true)"))
    for requete in ("UPDATE journal_securite SET succes = false WHERE evenement = 'TEST_INTEGRITE'",
                    "DELETE FROM journal_securite WHERE evenement = 'TEST_INTEGRITE'"):
        with pytest.raises(DBAPIError):
            with db.begin_nested():
                db.execute(text(requete))


# --------------------------------------------------------------------------- traçabilité et HTTP
def test_consultation_des_digicodes_tracee_une_fois_par_heure(client, db, jeu):
    jeton = entete(connecter(client, "test.livreur.a", jeu["secrets"]["a"]))
    for _ in range(3):                                   # l'interface relit la tournée à chaque clic
        assert client.get("/tournees/du-jour", headers=jeton).status_code == 200
    traces = db.scalars(select(JournalSecurite).where(
        JournalSecurite.evenement == "CONSULTATION_TOURNEE",
        JournalSecurite.identifiant_saisi == "test.livreur.a")).all()
    assert len(traces) == 1 and traces[0].details["tournee_id"] == jeu["tournee"].id


def test_entetes_de_securite_et_cors(client, db, jeu):
    jeton = entete(connecter(client, "test.livreur.a", jeu["secrets"]["a"]))
    r = client.get("/tournees/du-jour", headers=jeton)
    assert r.headers["X-Frame-Options"] == "DENY"                 # pas d'affichage dans une iframe
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert r.headers["Cache-Control"] == "no-store"               # digicodes jamais en cache
    # Un site tiers ne peut pas appeler l'API depuis le navigateur d'un utilisateur
    r = client.options("/auth/connexion", headers={"Origin": "https://site-malveillant.example",
                                                    "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
