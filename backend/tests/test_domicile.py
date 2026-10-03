"""Livreur interne / prestataire et adresse du domicile : départ et retour de la tournée.
Base PostgreSQL réelle, transaction annulée à la fin de chaque test (voir conftest.py)."""
import pytest
from sqlalchemy import select

from app.api.routes_tournees import geocodeur
from app.main import app
from app.models import CacheGeocodage, JournalSecurite, Livraison, Utilisateur
from app.models.enums import PrecisionGeocodage
from app.optim.matrice import Point, construire_matrice, construire_matrice_depart
from app.services.geocodage import ResultatGeocodage
from tests.outils import connecter, entete

DOMICILE = {"voie": "12 rue des Tilleuls", "code_postal": "69500", "ville": "Bron"}


class FauxGeocodeur:
    def __init__(self):
        self.appels = 0

    def geocoder(self, voie, code_postal, ville):
        self.appels += 1
        return ResultatGeocodage(45.73812345, 4.91234567, PrecisionGeocodage.ADRESSE)


@pytest.fixture
def geo():
    faux = FauxGeocodeur()
    app.dependency_overrides[geocodeur] = lambda: faux
    return faux


@pytest.fixture
def entetes(client, jeu):
    return {"livreur": entete(connecter(client, "test.livreur.a", jeu["secrets"]["a"])),
            "admin": entete(connecter(client, "test.admin.j7", jeu["secrets"]["admin"]))}


def _passer_interne(client, db, entetes, type_livreur="interne"):
    a = db.scalar(select(Utilisateur).where(Utilisateur.identifiant == "test.livreur.a"))
    r = client.post(f"/admin/utilisateurs/{a.id}/type-livreur", json={"type_livreur": type_livreur},
                    headers=entetes["admin"])
    assert r.status_code == 200, r.text
    return a, r.json()


def test_matrice_arrivee_differente_du_depot():
    depot, depart, domicile = Point(45.75, 4.85), Point(45.76, 4.84), Point(45.74, 4.91)
    clients = [Point(45.77, 4.86), Point(45.73, 4.88)]
    m = construire_matrice_depart(depart, depot, clients, 1.3, 30, arrivee=domicile)
    vers_domicile = construire_matrice([domicile, *clients], 1.3, 30)
    assert [m.distances_m[j][0] for j in (1, 2)] == [vers_domicile.distances_m[j][0] for j in (1, 2)]
    depuis_depart = construire_matrice([depart, *clients], 1.3, 30)
    assert [m.distances_m[0][j] for j in (1, 2)] == [depuis_depart.distances_m[0][j] for j in (1, 2)]
    # Sans arrivée particulière : comportement inchangé (retour au dépôt)
    m2 = construire_matrice_depart(depart, depot, clients, 1.3, 30)
    vers_depot = construire_matrice([depot, *clients], 1.3, 30)
    assert m2.distances_m[1][0] == vers_depot.distances_m[1][0]


def test_prestataire_sans_domicile(client, db, jeu, entetes, geo):
    r = client.get("/moi/domicile", headers=entetes["livreur"])
    assert r.status_code == 200 and r.json()["type_livreur"] == "prestataire"
    r = client.put("/moi/domicile", json=DOMICILE, headers=entetes["livreur"])
    assert r.status_code == 409 and geo.appels == 0
    assert client.get("/tournees/du-jour", headers=entetes["livreur"]).json()["retour"] == "depot"


def test_domicile_chiffre_et_cache_par_l_admin(client, db, jeu, entetes, geo):
    a, compte = _passer_interne(client, db, entetes)
    assert compte["type_livreur"] == "interne" and compte["domicile_renseigne"] is False

    r = client.put("/moi/domicile", json=DOMICILE, headers=entetes["livreur"])
    assert r.status_code == 200, r.text
    vue = r.json()
    assert vue["renseigne"] and "TILLEULS" in vue["adresse"] and vue["trajet_depot_min"] is not None

    # Chiffré en base (adresse et position), arrondi à 4 décimales, absent du cache partagé
    brut = db.connection().exec_driver_sql(
        "SELECT domicile_adresse, domicile_position FROM utilisateurs WHERE id = %s", (a.id,)).one()
    assert "TILLEULS" not in brut[0] and "45.7381" not in brut[1]
    db.expire_all()
    assert db.get(Utilisateur, a.id).domicile_position == "45.7381;4.9123"
    assert db.scalar(select(CacheGeocodage).where(CacheGeocodage.cle.like("%TILLEULS%"))) is None
    trace = db.scalar(select(JournalSecurite).where(JournalSecurite.evenement == "DOMICILE_MODIFIE",
                                                    JournalSecurite.utilisateur_id == a.id))
    assert trace is not None and "TILLEULS" not in str(trace.details)

    # L'administrateur sait seulement que l'adresse est renseignée
    compte = next(c for c in client.get("/admin/utilisateurs", headers=entetes["admin"]).json()
                  if c["identifiant"] == "test.livreur.a")
    assert compte["domicile_renseigne"] is True and "TILLEULS" not in str(compte)
    suivi = client.get(f"/admin/tournees/{jeu['tournee'].id}", headers=entetes["admin"]).json()
    assert suivi["domicile"] is None
    assert client.get("/tournees/du-jour", headers=entetes["livreur"]).json()["domicile"] is not None
    # Un livreur ne lit jamais l'adresse d'un autre : la route ne prend aucun identifiant
    assert client.get("/moi/domicile", headers=entetes["admin"]).status_code == 403


def test_retour_au_domicile_sauf_colis_non_livres(client, db, jeu, entetes, geo):
    _passer_interne(client, db, entetes)
    client.put("/moi/domicile", json=DOMICILE, headers=entetes["livreur"])

    # Client « hayon » incompatible avec le camion normal : il est à reporter -> retour au dépôt
    r = client.post("/tournees/du-jour/optimiser", headers=entetes["livreur"])
    assert r.status_code == 200 and r.json()["a_reporter"] == ["8899000005"]
    assert r.json()["retour"] == "depot"
    tournee = client.get("/tournees/du-jour", headers=entetes["livreur"]).json()
    assert tournee["retour"] == "depot" and tournee["colis_a_rapporter"] == 1

    # Sans ce client : tout peut être livré -> retour au domicile
    db.delete(db.scalar(select(Livraison).where(Livraison.client_id == "8899000005")))
    db.commit()
    r = client.post("/tournees/du-jour/optimiser", headers=entetes["livreur"])
    assert r.json()["retour"] == "domicile" and r.json()["a_reporter"] == []
    tournee = client.get("/tournees/du-jour", headers=entetes["livreur"]).json()
    assert tournee["retour"] == "domicile" and tournee["colis_a_rapporter"] == 0

    # Un client absent : son colis repart au dépôt
    premier = next(l for l in tournee["livraisons"] if l["ordre_passage"] == 1)
    client.post(f"/livraisons/{premier['id']}/demarrer", headers=entetes["livreur"])
    client.post(f"/livraisons/{premier['id']}/terminer", json={"issue": "absent"}, headers=entetes["livreur"])
    tournee = client.get("/tournees/du-jour", headers=entetes["livreur"]).json()
    assert tournee["retour"] == "depot" and tournee["colis_a_rapporter"] == 1
    r = client.post("/tournees/du-jour/optimiser", headers=entetes["livreur"])
    assert r.status_code == 200 and r.json()["mode"] == "restant" and r.json()["retour"] == "depot"


def test_passage_en_prestataire_efface_le_domicile(client, db, jeu, entetes, geo):
    a, _ = _passer_interne(client, db, entetes)
    client.put("/moi/domicile", json=DOMICILE, headers=entetes["livreur"])
    _, compte = _passer_interne(client, db, entetes, "prestataire")
    assert compte["type_livreur"] == "prestataire" and compte["domicile_renseigne"] is False
    db.expire_all()
    assert db.get(Utilisateur, a.id).domicile_adresse is None
    trace = db.scalar(select(JournalSecurite).where(JournalSecurite.evenement == "ADMIN_TYPE_LIVREUR")
                      .order_by(JournalSecurite.id.desc()))
    assert trace.details["domicile_efface"] is True


def test_creation_compte_avec_type(client, db, jeu, entetes):
    r = client.post("/admin/utilisateurs", headers=entetes["admin"], json={
        "identifiant": "test.interne", "nom": "Test", "prenom": "Interne", "role": "livreur",
        "type_livreur": "interne"})
    assert r.status_code == 201 and r.json()["compte"]["type_livreur"] == "interne"
    r = client.post("/admin/utilisateurs", headers=entetes["admin"], json={
        "identifiant": "test.presta", "nom": "Test", "prenom": "Presta", "role": "livreur"})
    assert r.json()["compte"]["type_livreur"] == "prestataire"                  # valeur par défaut
