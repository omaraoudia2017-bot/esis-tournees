"""Espace administrateur : import CSV, tournées du jour, clients (correction, RGPD), tableau de bord.

Fixtures `db`, `client`, `jeu` : voir conftest.py. Le géocodeur est remplacé par un faux.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.api.routes_tournees import geocodeur
from app.core.config import get_settings
from app.main import app
from app.models import Client, JournalSecurite, Livraison, Utilisateur
from app.models.enums import PrecisionGeocodage, SourceClient, StatutLivraison, TypeCamion
from app.services.admin import _dans_creneau
from app.services.geocodage import ResultatGeocodage
from app.services.tournees import aujourdhui
from tests.outils import connecter, entete
from tests.test_ajout_volee import FauxGeocodeur

PARIS = ZoneInfo("Europe/Paris")

CSV = """id_client;nom_client;adresse;telephone;digicode;creneau_horaire_prefere;historique_statut;notes_livraison
9912340001;Dupont Test;12 RUE DES ESSAIS ADMIN, 69002 LYON;0601020304;1234A;08H00-10H00;;IMMEUBLE ETAGE: 3 SANS ASC.
9912340002;Martin Test;5 AVENUE DES ESSAIS ADMIN 2EME ETAGE, 69007 LYON;;;14H00-16H00;;
9912340003;Ligne Fausse;8 RUE DU TEST, 69003 LYON;;;25H00-10H00;;
9912340001;Doublon;12 RUE DES ESSAIS ADMIN, 69002 LYON;;;08H00-10H00;;
"""


@pytest.fixture
def admin(client, jeu):
    return entete(connecter(client, "test.admin.j7", jeu["secrets"]["admin"]))


@pytest.fixture
def geo():
    faux = FauxGeocodeur(ResultatGeocodage(45.7605, 4.8490, PrecisionGeocodage.ADRESSE))
    app.dependency_overrides[geocodeur] = lambda: faux
    return faux


def nouveaux_clients(db, jeu, nombre, debut=1, camion=TypeCamion.NORMAL, **champs) -> list[str]:
    """Clients libres (dans aucune tournée), dans la zone de test (limite 10)."""
    zone_id = jeu["tournee"].zone_id
    ids = []
    for n in range(debut, debut + nombre):
        identifiant = f"88991000{n:02d}"
        db.add(Client(id_client=identifiant, nom_client=f"Libre {n}", adresse=f"{n} rue Libre",
                      code_postal="69003", ville="LYON", latitude=Decimal("45.75"),
                      longitude=Decimal(str(4.84 + n / 1000)), creneau_debut=time(8),
                      creneau_fin=time(12), type_camion_requis=camion, zone_id=zone_id,
                      digicode=f"{n}999", source=SourceClient.SIMULATION, **champs))
        ids.append(identifiant)
    db.commit()
    return ids


def livreur_id(db, identifiant) -> int:
    return db.scalar(select(Utilisateur.id).where(Utilisateur.identifiant == identifiant))


def dernier(db, evenement):
    return db.scalars(select(JournalSecurite).where(JournalSecurite.evenement == evenement)
                      .order_by(JournalSecurite.id.desc())).first()


# --------------------------------------------------------------------------- import CSV
def test_import_csv_controle_nettoyage_et_geocodage(client, db, admin, geo):
    r = client.post("/admin/import-csv", headers=admin,
                    json={"nom_fichier": "tournees_du_jour.csv", "contenu": CSV, "max_geocodages": 5})
    assert r.status_code == 200, r.text
    b = r.json()
    assert (b["lignes"], b["crees"], b["mis_a_jour"]) == (4, 2, 0)
    assert sorted((x["ligne"], x["motif"].split(" :")[0]) for x in b["rejets"]) == [
        (4, "créneau hors bornes"), (5, "doublon dans le fichier")]
    assert b["ids_valides"] == ["9912340001", "9912340002"]
    assert b["geocodage"]["traites"] == 2 and b["geocodage"]["precisions"] == {"adresse": 2}

    c = db.get(Client, "9912340002")
    assert c.adresse == "5 AVENUE DES ESSAIS ADMIN, 69007 LYON"          # complément retiré de l'adresse
    assert "2EME ETAGE" in c.notes_livraison                          # ... et rangé dans les notes chiffrées
    assert c.latitude is not None and c.zone_id is not None and c.source == SourceClient.IMPORT_CSV
    assert db.get(Client, "9912340001").etage == 3
    assert dernier(db, "IMPORT_CSV").details["crees"] == 2

    # Réimport : mise à jour, pas de doublon, pas de nouveau géocodage (adresse inchangée)
    r = client.post("/admin/import-csv", headers=admin, json={"nom_fichier": "bis.csv", "contenu": CSV})
    assert (r.json()["crees"], r.json()["mis_a_jour"]) == (0, 2) and geo.appels == 2


def test_import_csv_refuse_un_fichier_sans_les_colonnes_obligatoires(client, db, admin, geo):
    r = client.post("/admin/import-csv", headers=admin,
                    json={"nom_fichier": "faux.csv", "contenu": "nom;ville\nA;Lyon\n"})
    assert r.status_code == 422 and "adresse" in r.json()["detail"]
    assert dernier(db, "IMPORT_CSV").succes is False


def test_geocodage_par_lots(client, db, admin, geo):
    db.add(Client(id_client="9912340009", nom_client="Sans position", adresse="1 RUE X, 69003 LYON",
                  voie="1 RUE X", code_postal="69003", ville="LYON", source=SourceClient.IMPORT_CSV))
    db.commit()
    r = client.post("/admin/clients/geocoder", headers=admin, json={"maximum": 60})
    assert r.status_code == 200 and r.json()["restants"] == 0
    assert db.get(Client, "9912340009").latitude is not None


# --------------------------------------------------------------------------- tournées
def test_attribution_d_une_tournee_au_livreur(client, db, jeu, admin):
    ids = nouveaux_clients(db, jeu, 3)
    b = livreur_id(db, "test.livreur.b")
    r = client.post("/admin/tournees", headers=admin,
                    json={"livreur_id": b, "clients": ids, "periode": "apres-midi"})
    assert r.status_code == 201, r.text
    t = r.json()
    assert (t["livreur"], t["nb_clients"], t["max_clients"], t["statut"]) == ("test.livreur.b", 3, 10, "planifiee")
    assert t["heure_depart"] == "13:00:00" and not t["optimisee"]
    assert dernier(db, "ADMIN_ATTRIBUTION_TOURNEE").details["nb_clients"] == 3

    # Le livreur voit sa tournée, dans l'ordre fourni (à optimiser avec son bouton)
    jeton_b = connecter(client, "test.livreur.b", jeu["secrets"]["b"])
    vue = client.get("/tournees/du-jour", headers=entete(jeton_b)).json()
    assert [l["client"]["id_client"] for l in vue["livraisons"]] == ids
    assert all(l["temps_service_predit_s"] for l in vue["livraisons"])

    # L'admin suit la même tournée (carte, pointages)
    assert client.get(f"/admin/tournees/{t['id']}", headers=admin).json()["livraisons"][0]["client"]["digicode"]
    liste = client.get("/admin/tournees", headers=admin).json()
    assert {x["livreur"] for x in liste} >= {"test.livreur.a", "test.livreur.b"}

    # Deuxième tournée le même jour : refusée, sauf remplacement (tant qu'elle n'a pas commencé)
    assert client.post("/admin/tournees", headers=admin,
                       json={"livreur_id": b, "clients": ids}).status_code == 409
    r = client.post("/admin/tournees", headers=admin,
                    json={"livreur_id": b, "clients": ids[:2], "remplacer": True})
    assert r.status_code == 201 and r.json()["nb_clients"] == 2


def test_regles_de_refus_a_l_attribution(client, db, jeu, admin):
    b = livreur_id(db, "test.livreur.b")
    libres = nouveaux_clients(db, jeu, 11)
    hayon = nouveaux_clients(db, jeu, 1, debut=50, camion=TypeCamion.HAYON)

    def attribuer(clients, **autres):
        return client.post("/admin/tournees", headers=admin, json={"livreur_id": b, "clients": clients, **autres})

    r = attribuer(libres[:2] + hayon)
    assert r.status_code == 409 and hayon[0] in r.json()["detail"]          # camion inadapté
    r = attribuer(libres)
    assert r.status_code == 409 and "limite de 10" in r.json()["detail"]     # limite d'effectif
    r = attribuer(["8899000001"])
    assert r.status_code == 409 and "déjà" in r.json()["detail"]             # déjà chez le livreur A
    r = attribuer(["0000000000"])
    assert r.status_code == 422                                              # inconnu
    hier = (aujourdhui(get_settings()) - timedelta(days=1)).isoformat()
    assert attribuer(libres[:2], date_tournee=hier).status_code == 422       # dans le passé
    assert client.post("/admin/tournees", headers=admin, json={
        "livreur_id": livreur_id(db, "test.admin.j7"), "clients": libres[:2]}).status_code == 404
    assert db.scalar(select(Livraison).where(Livraison.client_id.in_(libres))) is None   # rien créé


def test_ajout_d_un_client_existant_par_l_admin(client, db, jeu, admin):
    (libre,) = nouveaux_clients(db, jeu, 1)
    (hayon,) = nouveaux_clients(db, jeu, 1, debut=60, camion=TypeCamion.HAYON)
    tournee_id = jeu["tournee"].id
    r = client.post(f"/admin/tournees/{tournee_id}/clients", headers=admin, json={"id_client": libre})
    assert r.status_code == 201, r.text
    assert len(client.get(f"/admin/tournees/{tournee_id}", headers=admin).json()["livraisons"]) == 6
    assert client.post(f"/admin/tournees/{tournee_id}/clients", headers=admin,
                       json={"id_client": libre}).status_code == 409             # déjà présent
    assert client.post(f"/admin/tournees/{tournee_id}/clients", headers=admin,
                       json={"id_client": hayon}).status_code == 409             # camion inadapté


# --------------------------------------------------------------------------- clients
def test_correction_d_un_client_mal_localise(client, db, jeu, admin):
    (ident,) = nouveaux_clients(db, jeu, 1, debut=70)
    c = db.get(Client, ident)
    c.source, c.geocode_precision = SourceClient.IMPORT_CSV, PrecisionGeocodage.COMMUNE
    db.commit()
    a_corriger = client.get("/admin/clients", headers=admin,
                            params={"a_corriger": True, "recherche": ident}).json()
    assert [x["id_client"] for x in a_corriger] == [ident]
    assert "digicode" not in a_corriger[0]                     # pas de donnée sensible dans les listes

    r = client.post(f"/admin/clients/{ident}/correction", headers=admin,
                    json={"latitude": 45.7701, "longitude": 4.8302, "type_camion_requis": "hayon"})
    assert r.status_code == 200, r.text
    assert (r.json()["precision"], r.json()["type_camion_requis"]) == ("manuelle", "hayon")
    assert r.json()["latitude"] == 45.7701 and r.json()["zone"]
    assert dernier(db, "ADMIN_CORRECTION_CLIENT").details["champs"] == ["position", "type_camion"]
    assert client.post(f"/admin/clients/{ident}/correction", headers=admin,
                       json={"latitude": 40.0, "longitude": 4.8}).status_code == 422   # hors France
    assert client.post(f"/admin/clients/{ident}/correction", headers=admin,
                       json={"latitude": 45.7}).status_code == 422                     # incomplet


def test_rgpd_export_puis_anonymisation(client, db, jeu, admin):
    (ident,) = nouveaux_clients(db, jeu, 1, debut=80, telephone="0611223344",
                                notes_livraison="Sonner chez Mme Libre")
    export = client.get(f"/admin/clients/{ident}/export", headers=admin).json()
    assert export["digicode"] == "80999" and export["telephone"] == "0611223344"
    assert export["livraisons"] == [] and dernier(db, "RGPD_EXPORT").details["client_id"] == ident

    # Un client attendu aujourd'hui ne peut pas être effacé
    r = client.post("/admin/clients/8899000001/anonymiser", headers=admin,
                    json={"confirmation": "8899000001"})
    assert r.status_code == 409
    assert client.post(f"/admin/clients/{ident}/anonymiser", headers=admin,
                       json={"confirmation": "8899000001"}).status_code == 422       # mauvaise confirmation
    r = client.post(f"/admin/clients/{ident}/anonymiser", headers=admin, json={"confirmation": ident})
    assert r.status_code == 200 and r.json()["nom"] == "CLIENT ANONYMISE" and not r.json()["actif"]
    assert r.json()["adresse"] == "69003 LYON" and r.json()["latitude"] is None
    export = client.get(f"/admin/clients/{ident}/export", headers=admin).json()
    assert export["telephone"] is None and export["digicode"] is None and export["notes_livraison"] is None
    assert dernier(db, "RGPD_ANONYMISATION").details["client_id"] == ident
    assert client.post(f"/admin/clients/{ident}/anonymiser", headers=admin,
                       json={"confirmation": ident}).status_code == 409             # déjà fait


# --------------------------------------------------------------------------- tableau de bord
def test_dans_creneau():
    c = Client(creneau_debut=time(8), creneau_fin=time(10))
    assert _dans_creneau(datetime(2026, 10, 1, 9, 30, tzinfo=PARIS), c, PARIS)
    assert not _dans_creneau(datetime(2026, 10, 1, 10, 5, tzinfo=PARIS), c, PARIS)
    assert _dans_creneau(datetime(2026, 10, 1, 23, 0, tzinfo=PARIS), Client(), PARIS)   # sans créneau


def test_tableau_de_bord(client, db, jeu, admin):
    avant = client.get("/admin/tableau-de-bord", headers=admin, params={"jours": 7}).json()
    jour = aujourdhui(get_settings())
    livraisons = db.scalars(select(Livraison).where(Livraison.tournee_id == jeu["tournee"].id)
                            .order_by(Livraison.id)).all()
    # Client 1 (créneau 8-10) servi à 9 h en 12 min ; client 2 (8-12) servi à 13 h en 20 min
    for l, heure, minutes in ((livraisons[0], 9, 12), (livraisons[1], 13, 20)):
        l.statut = StatutLivraison.LIVRE
        l.heure_debut_livraison = datetime.combine(jour, time(heure), tzinfo=PARIS)
        l.heure_fin_livraison = l.heure_debut_livraison + timedelta(minutes=minutes)
        l.temps_service_predit_s = 600
    db.commit()
    db.expire_all()                       # relit temps_service_reel_s, calculé par PostgreSQL

    r = client.get("/admin/tableau-de-bord", headers=admin, params={"jours": 7})
    assert r.status_code == 200, r.text
    td = r.json()
    assert td["creneaux_controles"] - avant["creneaux_controles"] == 2
    assert td["pointages_retenus"] - avant["pointages_retenus"] == 2
    ligne = next(t for t in td["par_tournee"] if t["livreur"] == "test.livreur.a")
    assert (ligne["clients"], ligne["limite"], ligne["faites"], ligne["remplissage"]) == (5, 10, 2, 50.0)
    assert td["par_jour"][-1]["date"] == jour.isoformat()
    # Créneaux : les pointages déjà en base (démos) comptent aussi -> 1 sur 2 de plus dans le créneau
    dans_avant = round((avant["taux_respect_creneaux"] or 0) * avant["creneaux_controles"] / 100)
    attendu = round(100 * (dans_avant + 1) / (avant["creneaux_controles"] + 2), 1)
    assert td["taux_respect_creneaux"] == attendu
    if avant["pointages_retenus"] == 0:   # aucun autre pointage plausible : écarts exacts
        assert td["ecart_moyen_predit_min"] == 6.0 and td["biais_predit_min"] == 6.0  # (2 + 10) / 2
        assert td["ecart_moyen_fixe_min"] == 6.0
