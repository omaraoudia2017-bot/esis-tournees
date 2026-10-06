"""Annuler l'attribution d'une tournée (« en attente »), la redonner à un autre livreur, et
calendrier du livreur (tournées passées et à venir, consultation seule hors du jour même).
Base PostgreSQL réelle, transaction annulée à la fin de chaque test (voir conftest.py)."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import JournalSecurite, Livraison, Tournee
from app.models.enums import StatutTournee
from app.services.tournees import aujourdhui
from tests.outils import connecter, entete


@pytest.fixture
def entetes(client, jeu):
    return {"a": entete(connecter(client, "test.livreur.a", jeu["secrets"]["a"])),
            "b": entete(connecter(client, "test.livreur.b", jeu["secrets"]["b"])),
            "admin": entete(connecter(client, "test.admin.j7", jeu["secrets"]["admin"]))}


def _id_livreur(client, entetes, identifiant):
    return next(c["id"] for c in client.get("/admin/utilisateurs", headers=entetes["admin"]).json()
                if c["identifiant"] == identifiant)


def _traces(db, evenement, tournee_id):
    return [t for t in db.scalars(select(JournalSecurite).where(JournalSecurite.evenement == evenement))
            if (t.details or {}).get("tournee_id") == tournee_id]


def test_annuler_l_attribution_puis_redonner(client, db, jeu, entetes):
    t = jeu["tournee"]
    r = client.post(f"/admin/tournees/{t.id}/desattribuer", headers=entetes["admin"])
    assert r.status_code == 200, r.text
    assert r.json()["livreur"] is None and r.json()["nb_clients"] == 5
    # L'ancien livreur ne la voit plus ; elle est listée « en attente » côté planificateur
    assert client.get("/tournees/du-jour", headers=entetes["a"]).status_code == 404
    assert t.id in [x["id"] for x in client.get("/admin/tournees/en-attente", headers=entetes["admin"]).json()]
    jour = [x for x in client.get("/admin/tournees", headers=entetes["admin"]).json() if x["id"] == t.id]
    assert jour and jour[0]["livreur_nom"] is None
    assert client.get(f"/admin/tournees/{t.id}", headers=entetes["admin"]).status_code == 200
    assert client.post(f"/admin/tournees/{t.id}/desattribuer", headers=entetes["admin"]).status_code == 409
    assert len(_traces(db, "ADMIN_DESATTRIBUTION_TOURNEE", t.id)) == 1

    # Redonner à B : refusé tant qu'un client exige un camion hayon (B a un camion normal)
    b = _id_livreur(client, entetes, "test.livreur.b")
    r = client.post(f"/admin/tournees/{t.id}/attribuer", json={"livreur_id": b}, headers=entetes["admin"])
    assert r.status_code == 409 and "incompatible" in r.json()["detail"]
    db.delete(db.scalar(select(Livraison).where(Livraison.tournee_id == t.id, Livraison.client_id == "8899000005")))
    db.commit()
    r = client.post(f"/admin/tournees/{t.id}/attribuer", json={"livreur_id": b}, headers=entetes["admin"])
    assert r.status_code == 200 and r.json()["livreur"] == "test.livreur.b"
    vue = client.get("/tournees/du-jour", headers=entetes["b"]).json()
    assert vue["id"] == t.id and vue["optimisee_le"] is None
    assert all(l["ordre_passage"] is None for l in vue["livraisons"])         # à réoptimiser par B
    assert t.id not in [x["id"] for x in client.get("/admin/tournees/en-attente", headers=entetes["admin"]).json()]
    trace = _traces(db, "ADMIN_REATTRIBUTION_TOURNEE", t.id)[0]
    assert trace.details["cible"] == "test.livreur.b" and trace.details["ancien"] is None

    # Transfert direct de B vers A (sans passer par « en attente »)
    a = _id_livreur(client, entetes, "test.livreur.a")
    r = client.post(f"/admin/tournees/{t.id}/attribuer", json={"livreur_id": a}, headers=entetes["admin"])
    assert r.status_code == 200 and r.json()["livreur"] == "test.livreur.a"
    assert client.get("/tournees/du-jour", headers=entetes["b"]).status_code == 404
    assert _traces(db, "ADMIN_REATTRIBUTION_TOURNEE", t.id)[-1].details["ancien"] == "test.livreur.b"


def test_controles_d_attribution(client, db, jeu, entetes):
    admin, t = entetes["admin"], jeu["tournee"]
    a, b = _id_livreur(client, entetes, "test.livreur.a"), _id_livreur(client, entetes, "test.livreur.b")
    dans2j = (aujourdhui(get_settings()) + timedelta(days=2)).isoformat()
    # Tournées dans 2 jours : une pour A, une pour B
    r = client.post("/admin/tournees", headers=admin, json={
        "livreur_id": a, "clients": ["8899000001", "8899000002"], "date_tournee": dans2j})
    assert r.status_code == 201, r.text
    tournee_a = r.json()["id"]
    assert client.post("/admin/tournees", headers=admin, json={
        "livreur_id": b, "clients": ["8899000003"], "date_tournee": dans2j}).status_code == 201
    # A a déjà une tournée ce jour-là : une 2e attribution est refusée, avec la date
    r = client.post("/admin/tournees", headers=admin, json={
        "livreur_id": a, "clients": ["8899000004"], "date_tournee": dans2j})
    assert r.status_code == 409 and "déjà une tournée" in r.json()["detail"]
    # B aussi : on ne peut pas lui donner celle de A
    r = client.post(f"/admin/tournees/{tournee_a}/attribuer", json={"livreur_id": b}, headers=admin)
    assert r.status_code == 409 and "déjà une tournée" in r.json()["detail"]
    assert client.post(f"/admin/tournees/{tournee_a}/attribuer", json={"livreur_id": a},
                       headers=admin).status_code == 409                      # déjà à lui

    # Tournée commencée : le livreur ne peut plus être changé
    db.get(Tournee, t.id).statut = StatutTournee.EN_COURS
    db.commit()
    assert client.post(f"/admin/tournees/{t.id}/desattribuer", headers=admin).status_code == 409
    assert client.post("/admin/tournees/999999/desattribuer", headers=admin).status_code == 404
    # Réservé au planificateur
    assert client.post(f"/admin/tournees/{tournee_a}/desattribuer", headers=entetes["a"]).status_code == 403
    assert client.get("/admin/tournees/en-attente", headers=entetes["a"]).status_code == 403


def test_calendrier_du_livreur(client, db, jeu, entetes):
    admin = entetes["admin"]
    auj = aujourdhui(get_settings())
    dans2j = (auj + timedelta(days=2)).isoformat()
    a = _id_livreur(client, entetes, "test.livreur.a")
    assert client.post("/admin/tournees", headers=admin, json={
        "livreur_id": a, "clients": ["8899000001", "8899000002"], "date_tournee": dans2j}).status_code == 201

    dates = {t["date_tournee"]: t for t in client.get("/tournees/mes-tournees", headers=entetes["a"]).json()}
    assert {auj.isoformat(), dans2j} <= set(dates) and dates[dans2j]["nb_clients"] == 2
    assert client.get("/tournees/mes-tournees", headers=entetes["b"]).json() == []

    # Autre jour : consultation seule, sans digicode, téléphone ni notes
    vue = client.get(f"/tournees/date/{dans2j}", headers=entetes["a"]).json()
    assert vue["consultation_seule"] and len(vue["livraisons"]) == 2
    assert all(l["client"]["digicode"] is None and l["client"]["telephone"] is None for l in vue["livraisons"])
    # Aujourd'hui : vue complète (digicodes visibles, comme l'écran du jour)
    vue = client.get(f"/tournees/date/{auj.isoformat()}", headers=entetes["a"]).json()
    assert not vue["consultation_seule"] and vue["livraisons"][0]["client"]["digicode"]

    # Cloisonnement : B ne voit pas les tournées de A ; pas de pointage hors du jour même
    assert client.get(f"/tournees/date/{dans2j}", headers=entetes["b"]).status_code == 404
    future = db.scalar(select(Livraison).join(Tournee, Tournee.id == Livraison.tournee_id)
                       .where(Tournee.date_tournee == auj + timedelta(days=2), Livraison.client_id == "8899000001"))
    assert client.post(f"/livraisons/{future.id}/demarrer", headers=entetes["a"]).status_code == 409
    assert client.get("/tournees/mes-tournees", headers=entetes["a"],
                      params={"du": dans2j, "au": auj.isoformat()}).status_code == 422
    assert client.get("/tournees/mes-tournees", headers=admin).status_code == 403
