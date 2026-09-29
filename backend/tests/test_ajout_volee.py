"""Module 3 : ajout d'une adresse en cours de tournée, règles de refus, recalcul du restant.

Le géocodeur est remplacé par un faux (aucun appel à Nominatim pendant les tests).
Fixtures `jeu`, `db`, `client` : voir conftest.py (tournée de 5 clients, limite 10, camion normal).
"""
import pytest
from sqlalchemy import select

from app.api.routes_tournees import geocodeur
from app.main import app
from app.models import Client, JournalSecurite, Livraison
from app.models.enums import PrecisionGeocodage
from app.services.geocodage import ResultatGeocodage
from tests.outils import connecter, entete

NOUVEAU = {"nom": "Boulangerie du Test", "voie": "999 rue des Tests Automatiques",
           "code_postal": "69003", "ville": "Lyon", "creneau_debut": "09:00", "creneau_fin": "12:00",
           "digicode": "4321B", "notes_livraison": "IMMEUBLE ETAGE: 2 AVEC ASC. Sonner deux fois"}


class FauxGeocodeur:
    def __init__(self, resultat: ResultatGeocodage):
        self.resultat, self.appels = resultat, 0

    def geocoder(self, voie, code_postal, ville):
        self.appels += 1
        return self.resultat


@pytest.fixture
def geo():
    faux = FauxGeocodeur(ResultatGeocodage(45.7605, 4.8490, PrecisionGeocodage.ADRESSE))
    app.dependency_overrides[geocodeur] = lambda: faux
    return faux


@pytest.fixture
def jeton(client, jeu):
    return connecter(client, "test.livreur.a", jeu["secrets"]["a"])


def nb_livraisons(db, jeu) -> int:
    return len(db.scalars(select(Livraison).where(Livraison.tournee_id == jeu["tournee"].id)).all())


def test_ajout_accepte(client, db, jeu, geo, jeton):
    r = client.post("/tournees/du-jour/clients", json=NOUVEAU, headers=entete(jeton))
    assert r.status_code == 201, r.text
    livraison = r.json()["livraison"]
    assert livraison["ajout_a_la_volee"] and livraison["statut"] == "a_livrer"
    assert livraison["ordre_passage"] is None and livraison["client"]["digicode"] == "4321B"

    nouveau = db.get(Client, livraison["client"]["id_client"])
    assert nouveau.id_client.startswith("7") and nouveau.source.value == "saisie_manuelle"
    assert nouveau.adresse == "999 RUE DES TESTS AUTOMATIQUES, 69003 LYON"
    assert nouveau.etage == 2 and nouveau.ascenseur is True           # extrait des notes
    assert nb_livraisons(db, jeu) == 6 and geo.appels == 1
    assert db.scalar(select(JournalSecurite).where(JournalSecurite.evenement == "AJOUT_CLIENT_VOLEE",
                                                   JournalSecurite.identifiant_saisi == "test.livreur.a"))


def test_refus_vehicule_incompatible(client, db, jeu, geo, jeton):
    r = client.post("/tournees/du-jour/clients", json={**NOUVEAU, "type_camion_requis": "hayon"},
                    headers=entete(jeton))
    assert r.status_code == 409 and "hayon" in r.json()["detail"]
    assert nb_livraisons(db, jeu) == 5 and geo.appels == 0             # refusé avant géocodage
    refus = db.scalar(select(JournalSecurite).where(JournalSecurite.evenement == "AJOUT_CLIENT_REFUSE",
                                                    JournalSecurite.identifiant_saisi == "test.livreur.a"))
    assert refus.details["motif"] == "vehicule_incompatible"


def test_refus_limite_effectif(client, db, jeu, geo, jeton):
    # La tournée de test a une limite de 10 : on la remplit avec 5 clients de plus
    for numero in range(1, 6):
        assert client.post("/tournees/du-jour/clients",
                           json={**NOUVEAU, "voie": f"{numero} rue des Tests Automatiques"},
                           headers=entete(jeton)).status_code == 201
    r = client.post("/tournees/du-jour/clients", json={**NOUVEAU, "voie": "50 rue des Tests"},
                    headers=entete(jeton))
    assert r.status_code == 409 and "limite" in r.json()["detail"].lower()
    assert nb_livraisons(db, jeu) == 10


def test_adresse_introuvable_et_saisie_invalide(client, db, jeu, geo, jeton):
    geo.resultat = ResultatGeocodage(None, None, PrecisionGeocodage.ECHEC)
    r = client.post("/tournees/du-jour/clients", json=NOUVEAU, headers=entete(jeton))
    assert r.status_code == 422 and "introuvable" in r.json()["detail"]
    # Validation des entrées : code postal, créneau incohérent, identifiant existant non accepté
    for erreur in ({"code_postal": "6900"}, {"creneau_debut": "12:00", "creneau_fin": "09:00"},
                   {"telephone": "<script>"}):
        assert client.post("/tournees/du-jour/clients", json={**NOUVEAU, **erreur},
                           headers=entete(jeton)).status_code == 422
    assert nb_livraisons(db, jeu) == 5


def test_recalcul_itineraire_restant(client, db, jeu, geo, jeton):
    ids = [l["id"] for l in client.get("/tournees/du-jour", headers=entete(jeton)).json()["livraisons"]]
    client.post("/tournees/du-jour/optimiser", headers=entete(jeton))
    premier = client.get("/tournees/du-jour", headers=entete(jeton)).json()["livraisons"][0]["id"]
    client.post(f"/livraisons/{premier}/demarrer", headers=entete(jeton))
    # Pas de recalcul pendant une livraison en cours
    assert client.post("/tournees/du-jour/optimiser", headers=entete(jeton)).status_code == 409
    client.post(f"/livraisons/{premier}/terminer", headers=entete(jeton))

    ajout = client.post("/tournees/du-jour/clients", json=NOUVEAU, headers=entete(jeton)).json()
    r = client.post("/tournees/du-jour/optimiser", headers=entete(jeton))
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "restant"

    livraisons = client.get("/tournees/du-jour", headers=entete(jeton)).json()["livraisons"]
    assert livraisons[0]["id"] == premier and livraisons[0]["statut"] == "livre"     # figée en tête
    assert [l["ordre_passage"] for l in livraisons] == list(range(1, len(ids) + 2))
    assert ajout["livraison"]["id"] in [l["id"] for l in livraisons[1:]]
    for l in livraisons[1:]:
        assert l["statut"] in ("a_livrer", "reporte")
        assert (l["heure_arrivee_prevue"] is not None) == (l["statut"] == "a_livrer")


def test_tournee_terminee_refuse_ajout_et_recalcul(client, db, jeu, geo, jeton):
    for l in client.get("/tournees/du-jour", headers=entete(jeton)).json()["livraisons"]:
        client.post(f"/livraisons/{l['id']}/demarrer", headers=entete(jeton))
        client.post(f"/livraisons/{l['id']}/terminer", headers=entete(jeton))
    assert client.post("/tournees/du-jour/clients", json=NOUVEAU, headers=entete(jeton)).status_code == 409
    assert client.post("/tournees/du-jour/optimiser", headers=entete(jeton)).status_code == 409
