"""Écran « Ma tournée » : liste du jour, bouton « Optimiser », pointage Démarrer / Terminer,
cloisonnement entre livreurs. Base PostgreSQL réelle, transaction annulée à la fin de chaque test
(fixtures `jeu`, `db`, `client` : voir conftest.py).
"""
from sqlalchemy import select

from app.models import JournalSecurite, Livraison
from tests.outils import connecter, entete


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
