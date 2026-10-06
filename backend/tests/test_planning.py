"""Planning des livreurs : une case par livreur et par jour, et tournées à attribuer.
Base PostgreSQL réelle, transaction annulée à la fin de chaque test (voir conftest.py)."""
from sqlalchemy import select

from app.core.config import get_settings
from app.models import JourneeTrajets, Utilisateur
from app.services.tournees import aujourdhui
from tests.outils import connecter, entete


def test_planning_de_la_semaine(client, db, jeu):
    admin = entete(connecter(client, "test.admin.j7", jeu["secrets"]["admin"]))
    auj = aujourdhui(get_settings())
    b = db.scalar(select(Utilisateur).where(Utilisateur.identifiant == "test.livreur.b"))
    db.add(JourneeTrajets(livreur_id=b.id, date_journee=auj))           # B : journée de trajets, sans tournée
    db.commit()

    r = client.get("/admin/planning", headers=admin, params={"debut": auj.isoformat()})
    assert r.status_code == 200, r.text
    p = r.json()
    assert len(p["jours"]) == 7 and p["jours"][0] == auj.isoformat()
    livreurs = {l["identifiant"]: l for l in p["livreurs"]}
    assert {"test.livreur.a", "test.livreur.b"} <= set(livreurs) and "test.admin.j7" not in livreurs
    assert livreurs["test.livreur.a"]["vehicule"] == "TEST-J7-N1"
    cases = {(c["livreur_id"], c["date"]): c for c in p["cases"]}
    a_case = cases[(livreurs["test.livreur.a"]["id"], auj.isoformat())]
    assert a_case["nature"] == "tournee" and a_case["nb_clients"] == 5 and a_case["tournee_id"] == jeu["tournee"].id
    assert cases[(b.id, auj.isoformat())]["nature"] == "trajets"

    # Attribution annulée : la case de A se libère, la tournée rejoint les tournées à attribuer,
    # avec le camion qu'exigent ses clients (un client palette -> hayon)
    assert client.post(f"/admin/tournees/{jeu['tournee'].id}/desattribuer", headers=admin).status_code == 200
    p = client.get("/admin/planning", headers=admin, params={"debut": auj.isoformat()}).json()
    assert (livreurs["test.livreur.a"]["id"], auj.isoformat()) not in {(c["livreur_id"], c["date"]) for c in p["cases"]}
    attente = next(t for t in p["a_attribuer"] if t["id"] == jeu["tournee"].id)
    assert attente["type_camion_requis"] == "hayon" and attente["livreur"] is None

    # Semaine en cours par défaut ; réservé au planificateur
    assert client.get("/admin/planning", headers=admin).json()["jours"][0] <= auj.isoformat()
    livreur = entete(connecter(client, "test.livreur.b", jeu["secrets"]["b"]))
    assert client.get("/admin/planning", headers=livreur).status_code == 403
