"""Répartition d'une liste de clients en tournées : classement par zone, plage horaire et type de
camion, découpage selon la limite de la zone, proposition de livreurs disponibles.
Base PostgreSQL réelle, transaction annulée à la fin de chaque test (voir conftest.py)."""
from datetime import time, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.models import Client, Vehicule, Zone
from app.models.enums import SourceClient, TypeCamion
from app.services.repartition import couper
from app.services.tournees import aujourdhui
from tests.outils import connecter, creer_compte, entete


def _client(db, identifiant, zone, lat, lon, creneau, cp="69997", camion=TypeCamion.NORMAL):
    db.add(Client(id_client=identifiant, nom_client=f"Client {identifiant[-3:]}", adresse=f"{identifiant[-2:]} rue Test",
                  code_postal=cp, ville="BRON", latitude=Decimal(str(lat)), longitude=Decimal(str(lon)),
                  creneau_debut=time(creneau[0]), creneau_fin=time(creneau[1]), type_camion_requis=camion,
                  zone_id=zone.id, source=SourceClient.SIMULATION))


@pytest.fixture
def liste(db, jeu):
    """30 clients d'une zone limitée à 10 : 25 du matin (2 codes postaux), 3 de l'après-midi,
    2 « hayon » du matin. Plus un livreur équipé d'un camion hayon."""
    zone = Zone(nom="Zone répartition test", max_clients=10)
    hayon = Vehicule(immatriculation="TEST-REP-H1", type_camion=TypeCamion.HAYON)
    db.add_all([zone, hayon])
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
    creer_compte(db, "test.livreur.hayon", vehicule_id=hayon.id)
    return ids


@pytest.fixture
def admin(client, jeu):
    return entete(connecter(client, "test.admin.j7", jeu["secrets"]["admin"]))


def test_classement_decoupage_et_livreurs(client, db, jeu, admin, liste):
    r = client.post("/admin/repartition", headers=admin, json={
        "clients": liste + ["8899000001", "0000000000"]})            # aujourd'hui par défaut
    assert r.status_code == 200, r.text
    p = r.json()

    # Écartés : client déjà dans la tournée de test.livreur.a aujourd'hui, identifiant inconnu
    motifs = {e["id_client"]: e["motif"] for e in p["exclus"]}
    assert "déjà dans une tournée" in motifs["8899000001"] and "inconnu" in motifs["0000000000"]

    groupes = p["groupes"]
    cles = [(g["periode"], g["type_camion"], len(g["clients"])) for g in groupes]
    # Matin normal : 25 clients pour une limite de 10 -> 3 tournées équilibrées (9, 8, 8)
    assert sorted(n for per, t, n in cles if per == "matin" and t == "normal") == [8, 8, 9]
    assert ("matin", "hayon", 2) in cles and ("apres-midi", "normal", 3) in cles
    # Matin avant après-midi ; aucun groupe ne dépasse sa limite ; chaque client placé une seule fois
    assert [g["periode"] for g in groupes].index("apres-midi") > [g["periode"] for g in groupes].index("matin")
    assert all(len(g["clients"]) <= g["limite"] == 10 for g in groupes)
    places = [c["id_client"] for g in groupes for c in g["clients"]]
    assert sorted(places) == sorted(liste)
    # Les codes postaux restent groupés : la 1re tournée du matin ne contient que 69997
    premier = next(g for g in groupes if g["periode"] == "matin" and g["type_camion"] == "normal")
    assert premier["codes_postaux"] == ["69997"]

    # Livreurs disponibles : pas test.livreur.a (déjà une tournée aujourd'hui)
    dispo = {l["identifiant"]: l for l in p["livreurs_disponibles"]}
    assert "test.livreur.a" not in dispo and {"test.livreur.b", "test.livreur.hayon"} <= set(dispo)
    par_id = {l["id"]: l for l in p["livreurs_disponibles"]}
    proposes = [g["livreur_propose_id"] for g in groupes if g["livreur_propose_id"]]
    assert len(proposes) == len(set(proposes)) >= 2            # un livreur par groupe, jamais deux fois
    # Le camion du livreur proposé correspond toujours au type du groupe
    assert all(par_id[g["livreur_propose_id"]]["type_camion"] == g["type_camion"]
               for g in groupes if g["livreur_propose_id"])
    assert next(g for g in groupes if g["type_camion"] == "hayon")["livreur_propose_id"] is not None

    # Un groupe proposé passe tous les contrôles de création de tournée
    g = premier
    r = client.post("/admin/tournees", headers=admin, json={
        "livreur_id": dispo["test.livreur.b"]["id"], "clients": [c["id_client"] for c in g["clients"]],
        "periode": g["periode"]})
    assert r.status_code == 201, r.text
    # Les clients placés sont ensuite écartés d'une nouvelle proposition
    p2 = client.post("/admin/repartition", headers=admin, json={"clients": liste}).json()
    assert len(p2["exclus"]) == len(g["clients"])
    assert "test.livreur.b" not in {l["identifiant"] for l in p2["livreurs_disponibles"]}


def test_journee_complete_et_controles(client, db, jeu, admin, liste):
    demain = aujourdhui(get_settings()) + timedelta(days=1)
    p = client.post("/admin/repartition", headers=admin, json={
        "clients": liste, "journee_continue": True, "date_tournee": demain.isoformat()}).json()
    assert {g["periode"] for g in p["groupes"]} == {"journee"}
    # 28 clients « normal » sur une seule plage -> 3 tournées ; 2 « hayon » -> 1
    assert sorted(len(g["clients"]) for g in p["groupes"] if g["type_camion"] == "normal") == [9, 9, 10]
    # Demain, test.livreur.a est libre
    assert "test.livreur.a" in {l["identifiant"] for l in p["livreurs_disponibles"]}

    hier = aujourdhui(get_settings()) - timedelta(days=1)
    assert client.post("/admin/repartition", headers=admin, json={
        "clients": liste, "date_tournee": hier.isoformat()}).status_code == 422
    assert client.post("/admin/repartition", headers=admin, json={"clients": []}).status_code == 422
    livreur = entete(connecter(client, "test.livreur.b", jeu["secrets"]["b"]))
    assert client.post("/admin/repartition", headers=livreur, json={"clients": liste}).status_code == 403
    # Aperçu (grille code postal x heures) : code postal et créneau, sans adresse ni contact
    apercu = client.post("/admin/repartition/apercu", headers=admin, json={"clients": liste + ["0000000000"]}).json()
    assert len(apercu) == len(liste) and {"69997", "69998"} <= {c["code_postal"] for c in apercu}
    assert all(c["localise"] for c in apercu) and "adresse" not in apercu[0] and "digicode" not in apercu[0]
    assert client.post("/admin/repartition/apercu", headers=livreur, json={"clients": liste}).status_code == 403
    # Recherche de clients par code postal (pour compléter la liste)
    trouves = client.get("/admin/clients", headers=admin, params={"recherche": "69998", "limite": 100}).json()
    assert {c["id_client"] for c in trouves} >= set(liste[15:25])


def test_couper_parts_egales_et_codes_postaux_groupes():
    reglages = get_settings()
    clients = [SimpleNamespace(code_postal="69002" if k % 2 else "69001", latitude=45.75 + k / 1000,
                               longitude=4.85) for k in range(23)]
    parts = couper(clients, 10, reglages)
    assert [len(p) for p in parts] == [8, 8, 7]
    assert {c.code_postal for c in parts[0]} == {"69001"}             # 12 « 69001 » d'abord
    assert len(couper(clients[:10], 10, reglages)) == 1
