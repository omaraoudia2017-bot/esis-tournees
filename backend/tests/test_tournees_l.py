"""Tournées « L » : génération à l'import (tournées réalisables, sans doublon), ajustements
(déplacer / retirer un client, supprimer) et attribution (date, livreur, période, heure de départ).
Base PostgreSQL réelle, transaction annulée à la fin de chaque test (voir conftest.py)."""
from datetime import time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import Client, Utilisateur, Zone
from app.models.enums import SourceClient, TypeCamion
from app.services.tournees import aujourdhui
from tests.outils import connecter, entete


def _client(db, identifiant, zone, lat, lon, creneau, cp="69997", camion=TypeCamion.NORMAL):
    db.add(Client(id_client=identifiant, nom_client=f"Client {identifiant[-3:]}", adresse=f"{identifiant[-2:]} rue Test",
                  code_postal=cp, ville="BRON", latitude=Decimal(str(lat)), longitude=Decimal(str(lon)),
                  creneau_debut=time(creneau[0]), creneau_fin=time(creneau[1]), type_camion_requis=camion,
                  zone_id=zone.id, source=SourceClient.SIMULATION))


@pytest.fixture
def liste(db, jeu):
    """30 clients d'une zone limitée à 10 : 25 du matin (2 codes postaux), 3 de l'après-midi,
    2 « hayon » du matin."""
    zone = Zone(nom="Zone tournées L test", max_clients=10)
    db.add(zone)
    db.flush()
    ids = []
    for k in range(25):
        ids.append(f"77100000{k:02d}")
        _client(db, ids[-1], zone, 45.73 + 0.002 * k, 4.90 + 0.001 * (k % 7), (8, 12), cp="69997" if k < 15 else "69998")
    for k in range(3):
        ids.append(f"77100001{k:02d}")
        _client(db, ids[-1], zone, 45.74, 4.91 + 0.002 * k, (14, 17))
    for k in range(2):
        ids.append(f"77100002{k:02d}")
        _client(db, ids[-1], zone, 45.75, 4.92, (9, 11), camion=TypeCamion.HAYON)
    db.commit()
    return ids


@pytest.fixture
def admin(client, jeu):
    return entete(connecter(client, "test.admin.j7", jeu["secrets"]["admin"]))


def _generer(client, admin, ids, **autres):
    r = client.post("/admin/tournees-l/generer", headers=admin, json={"clients": ids, **autres})
    assert r.status_code == 201, r.text
    return r.json()


def test_generation_realisable_et_sans_doublon(client, db, jeu, admin, liste):
    # Un client commandé plusieurs fois dans le fichier n'est livré qu'une fois
    rep = _generer(client, admin, liste + liste[:3] + ["0000000000"])
    assert [e["id_client"] for e in rep["exclus"]] == ["0000000000"]
    tournees = rep["tournees"]
    places = [c["id_client"] for t in tournees for c in t["clients"]]
    assert sorted(places) == sorted(liste)
    assert all(t["code"] == f"L{t['id']}" for t in tournees)
    # Matin, camion normal : 25 clients sur 8 h - 12 h, 2 par heure au plus -> 8 par tournée au plus
    cles = [(t["periode"], t["type_camion"], t["nb_clients"]) for t in tournees]
    assert sorted(n for per, typ, n in cles if per == "matin" and typ == "normal") == [1, 8, 8, 8]
    assert ("matin", "hayon", 2) in cles and ("apres-midi", "normal", 3) in cles
    assert all(max(t["charge_par_heure"].values()) <= 2 and t["depart_au_plus_tard"] for t in tournees)
    assert all([c["heure_visee"] for c in t["clients"]] == sorted(c["heure_visee"] for c in t["clients"])
               for t in tournees)                                                     # ordre horaire
    # Enregistrées : visibles dans la liste ; un 2e import des mêmes clients ne les duplique pas
    codes = {t["code"] for t in client.get("/admin/tournees-l", headers=admin).json()}
    assert {t["code"] for t in tournees} <= codes
    rep2 = _generer(client, admin, liste[:5])
    assert rep2["tournees"] == [] and all("déjà dans la tournée L" in e["motif"] for e in rep2["exclus"])
    # Journée complète : une seule plage
    assert client.get(f"/admin/tournees-l/{tournees[0]['id']}", headers=admin,
                      params={"periode": "journee"}).json()["debut_periode"] == "07:00:00"


def test_ajustements(client, db, jeu, admin, liste):
    tournees = _generer(client, admin, liste)["tournees"]
    normales = sorted((t for t in tournees if t["type_camion"] == "normal" and t["periode"] == "matin"),
                      key=lambda t: t["nb_clients"])
    petite, grande = normales[0], normales[-1]
    hayon = next(t for t in tournees if t["type_camion"] == "hayon")
    client_petite = petite["clients"][0]["id_client"]
    # Un client palette ne peut pas rejoindre une tournée en camion normal
    r = client.post(f"/admin/tournees-l/{hayon['id']}/clients/{hayon['clients'][0]['id_client']}/deplacer",
                    headers=admin, json={"vers": grande["id"]})
    assert r.status_code == 409 and "camion" in r.json()["detail"]
    # Déplacer le seul client de la petite tournée : elle disparaît
    assert client.post(f"/admin/tournees-l/{petite['id']}/clients/{client_petite}/deplacer", headers=admin,
                       json={"vers": grande["id"]}).status_code == 204
    assert client.get(f"/admin/tournees-l/{petite['id']}", headers=admin).status_code == 404
    assert client.get(f"/admin/tournees-l/{grande['id']}", headers=admin).json()["nb_clients"] == grande["nb_clients"] + 1
    # Retirer un client, supprimer une tournée L
    assert client.post(f"/admin/tournees-l/{grande['id']}/clients/{client_petite}/deplacer", headers=admin,
                       json={"vers": None}).status_code == 204
    assert client.delete(f"/admin/tournees-l/{hayon['id']}", headers=admin).status_code == 204
    restantes = {t["id"] for t in client.get("/admin/tournees-l", headers=admin).json()}
    assert hayon["id"] not in restantes and grande["id"] in restantes
    livreur = entete(connecter(client, "test.livreur.b", jeu["secrets"]["b"]))
    assert client.post("/admin/tournees-l/generer", headers=livreur, json={"clients": liste}).status_code == 403


def test_attribution_date_livreur_parametres(client, db, jeu, admin, liste):
    tournees = _generer(client, admin, liste)["tournees"]
    huit = [t for t in tournees if t["type_camion"] == "normal" and t["nb_clients"] == 8]
    b = db.scalar(select(Utilisateur).where(Utilisateur.identifiant == "test.livreur.b"))
    demain = (aujourdhui(get_settings()) + timedelta(days=1)).isoformat()
    r = client.post(f"/admin/tournees-l/{huit[0]['id']}/attribuer", headers=admin, json={
        "date_tournee": demain, "livreur_id": b.id, "periode": "journee", "heure_depart": "07:30"})
    assert r.status_code == 201, r.text
    t = r.json()
    assert (t["livreur"], t["date_tournee"], t["nb_clients"], t["heure_depart"]) == ("test.livreur.b", demain, 8, "07:30:00")
    assert huit[0]["id"] not in {x["id"] for x in client.get("/admin/tournees-l", headers=admin).json()}
    jeton_b = entete(connecter(client, "test.livreur.b", jeu["secrets"]["b"]))
    assert demain in {x["date_tournee"] for x in client.get("/tournees/mes-tournees", headers=jeton_b).json()}
    # B est déjà pris ce jour-là : refus, la tournée L reste à attribuer
    r = client.post(f"/admin/tournees-l/{huit[1]['id']}/attribuer", headers=admin,
                    json={"date_tournee": demain, "livreur_id": b.id})
    assert r.status_code == 409 and "déjà une tournée" in r.json()["detail"]
    assert huit[1]["id"] in {x["id"] for x in client.get("/admin/tournees-l", headers=admin).json()}
    # Heure de départ incohérente avec la période ; tournée déjà attribuée
    a = db.scalar(select(Utilisateur).where(Utilisateur.identifiant == "test.livreur.a"))
    assert client.post(f"/admin/tournees-l/{huit[1]['id']}/attribuer", headers=admin, json={
        "date_tournee": demain, "livreur_id": a.id, "periode": "matin", "heure_depart": "15:00"}).status_code == 422
    assert client.post(f"/admin/tournees-l/{huit[0]['id']}/attribuer", headers=admin,
                       json={"date_tournee": demain, "livreur_id": a.id}).status_code == 409
