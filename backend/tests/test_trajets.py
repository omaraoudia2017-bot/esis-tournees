"""Journée de trajets : les jours sans tournée, le livreur saisit ses étapes (garage, contrôle
technique…) et les fait optimiser en respectant ses rendez-vous.
Base PostgreSQL réelle, transaction annulée à la fin de chaque test (voir conftest.py)."""
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.api.routes_tournees import geocodeur
from app.core.config import get_settings
from app.main import app
from app.models import EtapeTrajet, JournalSecurite, Livraison, Utilisateur
from app.models.enums import PrecisionGeocodage
from app.services.geocodage import ResultatGeocodage
from tests.outils import connecter, entete

# Positions fictives dans l'agglomération lyonnaise, renvoyées l'une après l'autre
POSITIONS = [(45.7790, 4.8070), (45.7120, 4.8650), (45.7900, 4.9050), (45.7480, 4.9200)]
GARAGE = {"libelle": "Garage", "voie": "8 avenue du Test", "code_postal": "69009", "ville": "Lyon"}
CONTROLE = {"libelle": "Contrôle technique", "voie": "15 rue de l'Essai", "code_postal": "69007", "ville": "Lyon",
            "rdv_debut": "10:00", "rdv_fin": "10:30", "duree_min": 40}
PIECE = {"libelle": "Récupérer une pièce", "voie": "3 impasse Fictive", "code_postal": "69100",
         "ville": "Villeurbanne", "duree_min": 10}


class FauxGeocodeur:
    def __init__(self):
        self.appels = 0
        self.echec = False

    def geocoder(self, voie, code_postal, ville):
        self.appels += 1
        if self.echec:
            return ResultatGeocodage(None, None, PrecisionGeocodage.ECHEC)
        lat, lon = POSITIONS[(self.appels - 1) % len(POSITIONS)]
        return ResultatGeocodage(lat, lon, PrecisionGeocodage.ADRESSE)


@pytest.fixture
def geo():
    faux = FauxGeocodeur()
    app.dependency_overrides[geocodeur] = lambda: faux
    return faux


@pytest.fixture
def entetes(client, jeu):
    """test.livreur.a a une tournée aujourd'hui, test.livreur.b n'en a pas."""
    return {"a": entete(connecter(client, "test.livreur.a", jeu["secrets"]["a"])),
            "b": entete(connecter(client, "test.livreur.b", jeu["secrets"]["b"])),
            "admin": entete(connecter(client, "test.admin.j7", jeu["secrets"]["admin"]))}


def _ajouter(client, entetes, *etapes):
    ids = []
    for e in etapes:
        r = client.post("/trajets/du-jour/etapes", json=e, headers=entetes["b"])
        assert r.status_code == 201, r.text
        ids.append(r.json()["etape"]["id"])
    return ids


def _utilisateur(db, identifiant):
    return db.scalar(select(Utilisateur).where(Utilisateur.identifiant == identifiant))


def test_journee_saisie_optimisee_et_pointee(client, db, jeu, entetes, geo):
    livraisons_avant = db.scalar(select(func.count()).select_from(Livraison))
    vide = client.get("/trajets/du-jour", headers=entetes["b"]).json()
    assert vide["tournee_attribuee"] is False and vide["etapes"] == [] and vide["point_depart"]["nom"] == "Dépôt"

    ids = _ajouter(client, entetes, GARAGE, CONTROLE, PIECE)
    j = client.get("/trajets/du-jour", headers=entetes["b"]).json()
    assert [e["libelle"] for e in j["etapes"]] == ["Garage", "Contrôle technique", "Récupérer une pièce"]
    assert j["optimisee_le"] is None and j["etapes"][0]["duree_min"] == get_settings().trajets_duree_defaut_min

    r = client.post("/trajets/du-jour/optimiser", headers=entetes["b"])
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "complet" and r.json()["nb_etapes"] == 3 and r.json()["nb_retards"] == 0
    j = client.get("/trajets/du-jour", headers=entetes["b"]).json()
    assert j["optimisee_le"] and j["distance_km"] > 0 and j["duree_min"] > 0
    assert sorted(e["ordre"] for e in j["etapes"]) == [1, 2, 3]
    assert all(e["heure_prevue"] for e in j["etapes"])
    # Le rendez-vous du contrôle technique est respecté (attente sur place si on arrive en avance)
    controle = next(e for e in j["etapes"] if e["id"] == ids[1])
    arrivee = datetime.fromisoformat(controle["heure_prevue"]).astimezone(ZoneInfo(get_settings().fuseau_horaire))
    assert time(10, 0) <= arrivee.time() <= time(10, 30)

    # Pointage « Fait », double clic refusé, annulation, puis recalcul de l'itinéraire restant
    premiere = j["etapes"][0]["id"]
    assert client.post(f"/trajets/etapes/{premiere}/faite", headers=entetes["b"]).json()["faite_le"]
    assert client.post(f"/trajets/etapes/{premiere}/faite", headers=entetes["b"]).status_code == 409
    assert client.post(f"/trajets/etapes/{premiere}/a-faire", headers=entetes["b"]).json()["faite_le"] is None
    client.post(f"/trajets/etapes/{premiere}/faite", headers=entetes["b"])
    r = client.post("/trajets/du-jour/optimiser", headers=entetes["b"])
    assert r.json()["mode"] == "restant" and r.json()["nb_etapes"] == 2

    # Suppression d'une étape : l'itinéraire est à recalculer
    assert client.delete(f"/trajets/etapes/{ids[2]}", headers=entetes["b"]).status_code == 204
    j = client.get("/trajets/du-jour", headers=entetes["b"]).json()
    assert len(j["etapes"]) == 2 and j["optimisee_le"] is None

    # Ce ne sont pas des livraisons : ni tableau de bord, ni modèle de temps de service faussés
    assert db.scalar(select(func.count()).select_from(Livraison)) == livraisons_avant


def test_trajets_en_parallele_d_une_tournee(client, db, jeu, entetes, geo):
    # A a une tournée aujourd'hui : il peut quand même organiser des trajets à côté (page séparée)
    assert client.get("/trajets/du-jour", headers=entetes["a"]).json()["tournee_attribuee"] is True
    r = client.post("/trajets/du-jour/etapes", json=GARAGE, headers=entetes["a"])
    assert r.status_code == 201, r.text
    assert client.put("/trajets/du-jour/reglages", json={"depart": "depot", "retour": "depot", "heure_depart": "08:00"},
                      headers=entetes["a"]).status_code == 200
    assert client.post("/trajets/du-jour/optimiser", headers=entetes["a"]).status_code == 200
    # La tournée n'est pas touchée
    vue = client.get("/tournees/du-jour", headers=entetes["a"]).json()
    assert vue["id"] == jeu["tournee"].id and len(vue["livraisons"]) == 5


def test_cloisonnement_chiffrement_et_journal(client, db, jeu, entetes, geo):
    (etape_id,) = _ajouter(client, entetes, CONTROLE)
    a = _utilisateur(db, "test.livreur.a")

    # Un autre livreur ne peut ni supprimer ni pointer l'étape : 404, tentative tracée
    assert client.delete(f"/trajets/etapes/{etape_id}", headers=entetes["a"]).status_code == 404
    assert client.post(f"/trajets/etapes/{etape_id}/faite", headers=entetes["a"]).status_code == 404
    refus = db.scalars(select(JournalSecurite).where(JournalSecurite.evenement == "ACCES_REFUSE",
                                                     JournalSecurite.utilisateur_id == a.id)).all()
    assert sum(t.details.get("ressource") == "etape_trajet" and t.details.get("ressource_id") == etape_id
               for t in refus) == 2
    assert db.get(EtapeTrajet, etape_id) is not None

    # Libellé, adresse et position chiffrés en base ; jamais d'adresse dans le journal
    brut = db.connection().exec_driver_sql(
        "SELECT libelle, adresse, position FROM etapes_trajets WHERE id = %s", (etape_id,)).one()
    assert "Contrôle" not in brut[0] and "ESSAI" not in brut[1].upper() and "45.779" not in brut[2]
    b = _utilisateur(db, "test.livreur.b")
    trace = db.scalar(select(JournalSecurite).where(JournalSecurite.evenement == "TRAJET_ETAPE_AJOUTEE",
                                                    JournalSecurite.utilisateur_id == b.id))
    assert trace is not None and trace.details["etape_id"] == etape_id
    assert "ESSAI" not in str(trace.details).upper() and "Contrôle" not in str(trace.details)


def test_saisie_invalide(client, db, jeu, entetes, geo, monkeypatch):
    assert client.post("/trajets/du-jour/etapes", json={**GARAGE, "rdv_debut": "10:00"},
                       headers=entetes["b"]).status_code == 422
    assert client.post("/trajets/du-jour/etapes", json={**CONTROLE, "rdv_debut": "11:00", "rdv_fin": "10:00"},
                       headers=entetes["b"]).status_code == 422
    assert client.post("/trajets/du-jour/etapes", json={**GARAGE, "duree_min": 0},
                       headers=entetes["b"]).status_code == 422
    assert client.post("/trajets/du-jour/etapes", json={**GARAGE, "code_postal": "690"},
                       headers=entetes["b"]).status_code == 422
    geo.echec = True
    r = client.post("/trajets/du-jour/etapes", json=GARAGE, headers=entetes["b"])
    assert r.status_code == 422 and "introuvable" in r.json()["detail"]
    geo.echec = False
    monkeypatch.setattr(get_settings(), "trajets_max_etapes", 2)
    _ajouter(client, entetes, GARAGE, PIECE)
    assert client.post("/trajets/du-jour/etapes", json=CONTROLE, headers=entetes["b"]).status_code == 409


def test_depart_et_retour_au_domicile(client, db, jeu, entetes, geo):
    reglage = {"depart": "domicile", "retour": "domicile", "heure_depart": "07:30"}
    # Prestataire : pas de domicile possible
    assert client.put("/trajets/du-jour/reglages", json=reglage, headers=entetes["b"]).status_code == 422

    b = _utilisateur(db, "test.livreur.b")
    r = client.post(f"/admin/utilisateurs/{b.id}/type-livreur", json={"type_livreur": "interne"},
                    headers=entetes["admin"])
    assert r.status_code == 200, r.text
    assert client.put("/moi/domicile", json={"voie": "12 rue des Tilleuls", "code_postal": "69500", "ville": "Bron"},
                      headers=entetes["b"]).status_code == 200
    r = client.put("/trajets/du-jour/reglages", json=reglage, headers=entetes["b"])
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["domicile_disponible"] and j["point_depart"]["nom"] == j["point_arrivee"]["nom"] == "Domicile"
    assert j["heure_depart"] == "07:30:00"
    _ajouter(client, entetes, GARAGE)
    assert client.post("/trajets/du-jour/optimiser", headers=entetes["b"]).status_code == 200

    # Le planificateur voit « domicile », jamais l'adresse ni la position du domicile
    vue = next(x for x in client.get("/admin/trajets", headers=entetes["admin"]).json()
               if x["livreur"] == "test.livreur.b")
    assert vue["depart"] == "domicile" and "TILLEULS" not in str(vue).upper() and "latitude" not in str(vue)


def test_planificateur_en_lecture_seule(client, db, jeu, entetes, geo):
    ids = _ajouter(client, entetes, GARAGE, CONTROLE)
    r = client.get("/admin/trajets", headers=entetes["admin"])
    assert r.status_code == 200
    vue = next(x for x in r.json() if x["livreur"] == "test.livreur.b")
    assert [e["libelle"] for e in vue["etapes"]] == ["Garage", "Contrôle technique"]
    assert vue["optimisee"] is False and vue["nb_faites"] == 0
    # Aucune route d'administration ne modifie les étapes ; un livreur n'accède pas à la vue
    assert client.get("/admin/trajets", headers=entetes["b"]).status_code == 403
    assert client.delete(f"/trajets/etapes/{ids[0]}", headers=entetes["admin"]).status_code == 403
