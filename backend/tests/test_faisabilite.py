"""Faisabilité d'une tournée : au plus N clients par heure, limite selon la période, heure de
départ du dépôt au plus tard. Fonctions pures + affichage dans la tournée du livreur."""
from app.optim.faisabilite import (charge_par_heure, depart_au_plus_tard_s, heures_possibles,
                                   limite_periode, placer_par_heure)
from app.optim.matrice import Point, construire_matrice
from app.optim.probleme import ClientTournee
from app.optim.segmentation import Periode
from tests.outils import connecter, entete

H = 3600
MATIN = Periode("matin", 7 * H, 14 * H, 13 * H)
APRES_MIDI = Periode("apres-midi", 13 * H, 19 * H, 18 * H)
SOIREE = Periode("soiree", 17 * H, 22 * H, 24 * H)
JOURNEE = Periode("journee", 7 * H, 22 * H, 24 * H)


def _client(nom, debut_h, fin_h, service_min=10):
    return ClientTournee(nom, int(debut_h * H), int(fin_h * H), service_min * 60)


def test_limite_selon_la_periode():
    # 2 clients par heure : 20 clients seulement sur la journée complète
    assert [limite_periode(p, 20, 2) for p in (MATIN, APRES_MIDI, SOIREE, JOURNEE)] == [14, 12, 10, 20]
    assert limite_periode(MATIN, 10, 2) == 10                  # jamais plus que la limite de la zone
    assert limite_periode(SOIREE, 20, 1) == 10                 # plancher de la base (10 à 20 clients)


def test_deux_clients_au_plus_par_heure():
    # 3 clients sur 8 h - 9 h : le 3e ne peut pas être placé ; un client 8 h - 12 h passe à 9 h
    clients = [_client("a", 8, 9), _client("b", 8, 9), _client("c", 8, 9), _client("d", 8, 12)]
    places, refuses = placer_par_heure(clients, MATIN, 2)
    assert refuses == [2] and places == {0: 8, 1: 8, 3: 9}
    assert charge_par_heure(list(places.values())) == {8: 2, 9: 1}
    # Créneau 8 h 30 - 10 h : heures 8 et 9 ; créneau hors période : toutes les heures de la période
    assert heures_possibles(_client("e", 8.5, 10), MATIN) == [8, 9]
    assert heures_possibles(_client("f", 20, 21), MATIN) == list(range(7, 14))
    # 19 clients le matin : au plus 14 placés sur une tournée (7 heures x 2)
    matin = [_client(str(k), 8 + k % 5, 9 + k % 5 + 1) for k in range(19)]
    places, refuses = placer_par_heure(matin, MATIN, 2)
    assert max(charge_par_heure(list(places.values())).values()) <= 2 and len(refuses) > 0


def test_depart_au_plus_tard():
    depot, p1, p2 = Point(45.70, 4.77), Point(45.75, 4.85), Point(45.76, 4.86)
    m = construire_matrice([depot, p1, p2], 1.3, 30)
    clients = [_client("a", 8, 9), _client("b", 8, 9)]
    depart = depart_au_plus_tard_s(m, clients)
    # Le 2e client doit commencer avant 9 h : le 1er avant 9 h - 10 min - trajet ; puis trajet du dépôt
    attendu = min(9 * H, 9 * H - 600 - m.durees_s[1][2]) - m.durees_s[0][1]
    assert depart == attendu and 8 * H < depart < 9 * H
    assert depart_au_plus_tard_s(m, []) is None


def test_depart_au_plus_tard_affiche_au_livreur(client, db, jeu):
    vue = client.get("/tournees/du-jour", headers=entete(connecter(client, "test.livreur.a", jeu["secrets"]["a"]))).json()
    assert vue["statut"] == "planifiee" and vue["depart_depot_au_plus_tard"] is not None


def test_apercu_avant_attribution(client, db, jeu):
    admin = entete(connecter(client, "test.admin.j7", jeu["secrets"]["admin"]))
    ids = [f"88990000{k:02d}" for k in range(1, 6)] + ["0000000000"]
    r = client.post("/admin/tournees/apercu", headers=admin, json={"clients": ids, "periode": "matin"})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["inconnus"] == ["0000000000"] and len(a["clients"]) == 5 and a["limite"] == 14   # matin : 7 h x 2
    heures = [c["heure_visee"] for c in a["clients"]]
    assert heures == sorted(heures) and max(a["charge_par_heure"].values()) <= 2           # ordre horaire
    assert a["depart_au_plus_tard"] is not None and a["refuses"] == []
    # N'enregistre rien ; réservé au planificateur
    livreur = entete(connecter(client, "test.livreur.b", jeu["secrets"]["b"]))
    assert client.post("/admin/tournees/apercu", headers=livreur, json={"clients": ids}).status_code == 403
