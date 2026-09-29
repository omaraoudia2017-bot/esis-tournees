"""Écran « Ma tournée » : liste du jour, bouton « Optimiser », pointage Démarrer / Terminer,
cloisonnement entre livreurs. Base PostgreSQL réelle, transaction annulée à la fin de chaque test.
"""
from datetime import time
from decimal import Decimal

import pyotp
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import Client, JournalSecurite, Livraison, Tournee, Vehicule, Zone
from app.models.enums import RoleUtilisateur, SourceClient, TypeCamion
from app.services.tournees import aujourdhui
from tests.outils import connecter, creer_compte, entete

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


def test_liste_brute_puis_optimisation(client, db, jeu):
    jeton = connecter(client, "test.livreur.a", jeu["secrets"]["a"])
    r = client.get("/tournees/du-jour", headers=entete(jeton))
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    tournee = r.json()
    assert tournee["statut"] == "planifiee" and len(tournee["livraisons"]) == 5
    assert all(l["ordre_passage"] is None for l in tournee["livraisons"])      # liste brute
    assert tournee["livraisons"][0]["client"]["digicode"] == "1234A"           # déchiffré pour lui

    r = client.post("/tournees/du-jour/optimiser", headers=entete(jeton))
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["nb_optimises"] == 4 and res["a_reporter"] == ["8899000005"]    # hayon ≠ camion normal
    assert res["distance_apres_km"] <= res["distance_avant_km"]

    livraisons = client.get("/tournees/du-jour", headers=entete(jeton)).json()["livraisons"]
    assert [l["ordre_passage"] for l in livraisons] == [1, 2, 3, 4, 5]
    assert livraisons[-1]["statut"] == "reporte" and livraisons[-1]["heure_arrivee_prevue"] is None
    assert all(l["heure_arrivee_prevue"] for l in livraisons[:4])


def test_pointage_demarrer_terminer(client, db, jeu):
    jeton = connecter(client, "test.livreur.a", jeu["secrets"]["a"])
    ids = [l["id"] for l in client.get("/tournees/du-jour", headers=entete(jeton)).json()["livraisons"]]

    assert client.post(f"/livraisons/{ids[0]}/terminer", headers=entete(jeton)).status_code == 409
    r = client.post(f"/livraisons/{ids[0]}/demarrer", headers=entete(jeton))
    assert r.status_code == 200 and r.json()["statut"] == "en_cours"
    assert r.json()["heure_debut_livraison"] is not None
    # Une seule livraison en cours à la fois
    assert client.post(f"/livraisons/{ids[1]}/demarrer", headers=entete(jeton)).status_code == 409
    # La tournée a commencé : plus de réoptimisation complète
    assert client.post("/tournees/du-jour/optimiser", headers=entete(jeton)).status_code == 409

    r = client.post(f"/livraisons/{ids[0]}/terminer", json={"issue": "livre"}, headers=entete(jeton))
    assert r.status_code == 200, r.text
    assert r.json()["statut"] == "livre" and r.json()["temps_service_reel_s"] is not None
    assert client.post(f"/livraisons/{ids[0]}/demarrer", headers=entete(jeton)).status_code == 409

    # Client absent : l'issue est enregistrée, le temps passé aussi
    client.post(f"/livraisons/{ids[1]}/demarrer", headers=entete(jeton))
    r = client.post(f"/livraisons/{ids[1]}/terminer", json={"issue": "absent"}, headers=entete(jeton))
    assert r.json()["statut"] == "absent"
    assert client.post(f"/livraisons/{ids[2]}/demarrer", headers=entete(jeton)).status_code == 200
    assert client.post(f"/livraisons/{ids[2]}/terminer", json={"issue": "perdu"},
                       headers=entete(jeton)).status_code == 422

    db.refresh(jeu["tournee"])
    assert jeu["tournee"].statut.value == "en_cours"


def test_fin_de_tournee(client, db, jeu):
    jeton = connecter(client, "test.livreur.a", jeu["secrets"]["a"])
    for l in client.get("/tournees/du-jour", headers=entete(jeton)).json()["livraisons"]:
        client.post(f"/livraisons/{l['id']}/demarrer", headers=entete(jeton))
        client.post(f"/livraisons/{l['id']}/terminer", headers=entete(jeton))
    assert client.get("/tournees/du-jour", headers=entete(jeton)).json()["statut"] == "terminee"


def test_cloisonnement_entre_livreurs(client, db, jeu):
    jeton_b = connecter(client, "test.livreur.b", jeu["secrets"]["b"])
    jeton_admin = connecter(client, "test.admin.j7", jeu["secrets"]["admin"])
    id_de_a = db.scalar(select(Livraison.id).where(Livraison.tournee_id == jeu["tournee"].id).limit(1))

    # B n'a pas de tournée et ne peut pas pointer celle de A (même réponse qu'un id inexistant)
    assert client.get("/tournees/du-jour", headers=entete(jeton_b)).status_code == 404
    r = client.post(f"/livraisons/{id_de_a}/demarrer", headers=entete(jeton_b))
    assert r.status_code == 404
    assert r.json() == client.post("/livraisons/999999999/demarrer", headers=entete(jeton_b)).json()
    trace = db.scalars(select(JournalSecurite).where(JournalSecurite.evenement == "ACCES_REFUSE",
                                                     JournalSecurite.identifiant_saisi == "test.livreur.b"))
    assert any(t.details.get("ressource_id") == id_de_a for t in trace)

    # L'écran livreur est réservé au rôle livreur ; sans jeton : 401
    assert client.get("/tournees/du-jour", headers=entete(jeton_admin)).status_code == 403
    assert client.get("/tournees/du-jour").status_code == 401
    assert db.get(Livraison, id_de_a).statut.value == "a_livrer"
