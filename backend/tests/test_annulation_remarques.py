"""Annulation du dernier clic (Démarrer / Terminer) et remarques sur une adresse.
Base PostgreSQL réelle, transaction annulée à la fin de chaque test (voir conftest.py)."""
from datetime import timedelta

from sqlalchemy import select

from app.models import Client, JournalSecurite, Livraison, RemarqueClient
from tests.outils import connecter, entete

ANNULER = "/tournees/du-jour/annuler-dernier-pointage"


def _traces(db, evenement, **filtre):
    """Traces du journal produites par CE test : le journal est inaltérable et contient déjà les
    traces des essais faits à l'écran, on filtre donc sur la tournée ou la remarque du test."""
    lignes = db.scalars(select(JournalSecurite).where(JournalSecurite.evenement == evenement)
                        .order_by(JournalSecurite.id)).all()
    return [t for t in lignes if all((t.details or {}).get(k) == v for k, v in filtre.items())]


def _livraisons(client, jeton):
    return client.get("/tournees/du-jour", headers=entete(jeton)).json()["livraisons"]


def test_annuler_terminer_puis_demarrer(client, db, jeu):
    jeton = connecter(client, "test.livreur.a", jeu["secrets"]["a"])
    ids = [l["id"] for l in _livraisons(client, jeton)]
    assert client.post(ANNULER, headers=entete(jeton)).status_code == 409        # rien à annuler

    client.post(f"/livraisons/{ids[0]}/demarrer", headers=entete(jeton))
    client.post(f"/livraisons/{ids[0]}/terminer", json={"issue": "livre"}, headers=entete(jeton))
    tournee = client.get("/tournees/du-jour", headers=entete(jeton)).json()
    assert tournee["annulation_possible"]["action"] == "terminer"
    assert tournee["annulation_possible"]["livraison_id"] == ids[0]
    assert db.get(Client, "8899000001").derniere_livraison_le is not None

    # 1er clic annulé : « Terminer » -> la livraison est de nouveau en cours
    r = client.post(ANNULER, headers=entete(jeton))
    assert r.status_code == 200, r.text
    assert r.json()["action_annulee"] == "terminer"
    l = r.json()["livraison"]
    assert l["statut"] == "en_cours" and l["heure_fin_livraison"] is None
    assert l["temps_service_reel_s"] is None
    db.expire_all()
    assert db.get(Client, "8899000001").derniere_livraison_le is None              # RGPD : date restaurée

    # 2e clic annulé : « Démarrer » -> à livrer, la tournée redevient planifiée
    r = client.post(ANNULER, headers=entete(jeton))
    assert r.json()["action_annulee"] == "demarrer" and r.json()["livraison"]["statut"] == "a_livrer"
    tournee = client.get("/tournees/du-jour", headers=entete(jeton)).json()
    assert tournee["statut"] == "planifiee" and tournee["annulation_possible"] is None

    traces = _traces(db, "POINTAGE_ANNULE", tournee_id=jeu["tournee"].id)
    assert [t.details["action"] for t in traces] == ["terminer", "demarrer"]


def test_seul_le_dernier_clic_est_annulable(client, db, jeu):
    jeton = connecter(client, "test.livreur.a", jeu["secrets"]["a"])
    ids = [l["id"] for l in _livraisons(client, jeton)]
    for i in ids[:2]:
        client.post(f"/livraisons/{i}/demarrer", headers=entete(jeton))
        client.post(f"/livraisons/{i}/terminer", json={"issue": "absent"}, headers=entete(jeton))
    r = client.post(ANNULER, headers=entete(jeton))
    assert r.json()["livraison"]["id"] == ids[1]                                   # le plus récent
    db.expire_all()
    assert db.get(Livraison, ids[0]).statut.value == "absent"                     # inchangé


def test_fin_de_tournee_annulable(client, db, jeu):
    jeton = connecter(client, "test.livreur.a", jeu["secrets"]["a"])
    for l in _livraisons(client, jeton):
        client.post(f"/livraisons/{l['id']}/demarrer", headers=entete(jeton))
        client.post(f"/livraisons/{l['id']}/terminer", headers=entete(jeton))
    assert client.get("/tournees/du-jour", headers=entete(jeton)).json()["statut"] == "terminee"
    assert client.post(ANNULER, headers=entete(jeton)).status_code == 200
    assert client.get("/tournees/du-jour", headers=entete(jeton)).json()["statut"] == "en_cours"


def test_delai_d_annulation_depasse(client, db, jeu):
    jeton = connecter(client, "test.livreur.a", jeu["secrets"]["a"])
    premier = _livraisons(client, jeton)[0]["id"]
    client.post(f"/livraisons/{premier}/demarrer", headers=entete(jeton))
    livraison = db.get(Livraison, premier)
    livraison.heure_debut_livraison -= timedelta(minutes=11)                      # clic il y a 11 min
    db.commit()
    assert client.get("/tournees/du-jour", headers=entete(jeton)).json()["annulation_possible"] is None
    assert client.post(ANNULER, headers=entete(jeton)).status_code == 409


def test_un_autre_livreur_ne_peut_rien_annuler(client, db, jeu):
    jeton_a = connecter(client, "test.livreur.a", jeu["secrets"]["a"])
    jeton_b = connecter(client, "test.livreur.b", jeu["secrets"]["b"])
    premier = _livraisons(client, jeton_a)[0]["id"]
    client.post(f"/livraisons/{premier}/demarrer", headers=entete(jeton_a))
    assert client.post(ANNULER, headers=entete(jeton_b)).status_code == 404       # B n'a pas de tournée
    db.expire_all()
    assert db.get(Livraison, premier).statut.value == "en_cours"


def test_annuler_demarrage_sans_delai_puis_demarrer_un_autre_client(client, db, jeu):
    jeton = connecter(client, "test.livreur.a", jeu["secrets"]["a"])
    ids = [l["id"] for l in _livraisons(client, jeton)]
    # N'importe quel client peut être démarré, pas forcément le premier de la liste
    assert client.post(f"/livraisons/{ids[3]}/demarrer", headers=entete(jeton)).status_code == 200
    livraison = db.get(Livraison, ids[3])
    livraison.heure_debut_livraison -= timedelta(minutes=45)                      # démarré il y a 45 min
    db.commit()
    assert client.post(f"/livraisons/{ids[1]}/demarrer", headers=entete(jeton)).status_code == 409

    r = client.post(f"/livraisons/{ids[3]}/annuler-demarrage", headers=entete(jeton))
    assert r.status_code == 200, r.text                                           # pas de délai
    assert r.json()["statut"] == "a_livrer" and r.json()["heure_debut_livraison"] is None
    assert client.get("/tournees/du-jour", headers=entete(jeton)).json()["statut"] == "planifiee"
    assert client.post(f"/livraisons/{ids[1]}/demarrer", headers=entete(jeton)).status_code == 200

    # Livraison non démarrée ou déjà terminée : rien à annuler
    assert client.post(f"/livraisons/{ids[3]}/annuler-demarrage", headers=entete(jeton)).status_code == 409
    client.post(f"/livraisons/{ids[1]}/terminer", json={"issue": "livre"}, headers=entete(jeton))
    assert client.post(f"/livraisons/{ids[1]}/annuler-demarrage", headers=entete(jeton)).status_code == 409

    traces = _traces(db, "POINTAGE_ANNULE", tournee_id=jeu["tournee"].id)
    assert [(t.details["action"], t.details["livraison_id"]) for t in traces] == [("demarrer", ids[3])]


def test_annuler_demarrage_d_un_collegue_impossible(client, db, jeu):
    jeton_a = connecter(client, "test.livreur.a", jeu["secrets"]["a"])
    jeton_b = connecter(client, "test.livreur.b", jeu["secrets"]["b"])
    premier = _livraisons(client, jeton_a)[0]["id"]
    client.post(f"/livraisons/{premier}/demarrer", headers=entete(jeton_a))
    assert client.post(f"/livraisons/{premier}/annuler-demarrage", headers=entete(jeton_b)).status_code == 404
    db.expire_all()
    assert db.get(Livraison, premier).statut.value == "en_cours"


def test_remarques_ajout_affichage_suppression(client, db, jeu):
    jeton_a = connecter(client, "test.livreur.a", jeu["secrets"]["a"])
    jeton_b = connecter(client, "test.livreur.b", jeu["secrets"]["b"])
    premier = _livraisons(client, jeton_a)[0]

    r = client.post(f"/livraisons/{premier['id']}/remarques",
                    json={"texte": "  Portail fermé le matin,\n sonner au 2e.  "}, headers=entete(jeton_a))
    assert r.status_code == 201, r.text
    remarque = r.json()
    assert remarque["texte"] == "Portail fermé le matin, sonner au 2e." and remarque["de_moi"] is True

    # Visible sur la carte du client à la prochaine lecture de la tournée
    vue = next(l for l in _livraisons(client, jeton_a) if l["id"] == premier["id"])
    assert [x["texte"] for x in vue["client"]["remarques"]] == [remarque["texte"]]

    # Chiffrée en base, jamais recopiée dans le journal
    brut = db.connection().exec_driver_sql(
        "SELECT texte FROM remarques_clients WHERE id = %s", (remarque["id"],)).scalar()
    assert "Portail" not in brut
    [trace] = _traces(db, "REMARQUE_AJOUTEE", remarque_id=remarque["id"])
    assert "Portail" not in str(trace.details)

    # Cloisonnement : B ne peut ni écrire sur la tournée de A, ni supprimer la remarque de A
    assert client.post(f"/livraisons/{premier['id']}/remarques", json={"texte": "test intrusion"},
                       headers=entete(jeton_b)).status_code == 404
    assert client.delete(f"/remarques/{remarque['id']}", headers=entete(jeton_b)).status_code == 404

    # Trop courte / trop longue : refusée
    for texte in ("ok", "x" * 301):
        assert client.post(f"/livraisons/{premier['id']}/remarques", json={"texte": texte},
                           headers=entete(jeton_a)).status_code == 422

    assert client.delete(f"/remarques/{remarque['id']}", headers=entete(jeton_a)).status_code == 204
    assert db.get(RemarqueClient, remarque["id"]) is None


def test_remarques_admin_export_et_anonymisation(client, db, jeu):
    jeton_a = connecter(client, "test.livreur.a", jeu["secrets"]["a"])
    jeton_admin = connecter(client, "test.admin.j7", jeu["secrets"]["admin"])
    livraisons = _livraisons(client, jeton_a)
    dernier = livraisons[-1]                    # client 5 (hayon) : on le retire de la tournée
    client.post(f"/livraisons/{dernier['id']}/remarques", json={"texte": "Quai de déchargement à gauche"},
                headers=entete(jeton_a))
    id_client = dernier["client"]["id_client"]

    r = client.get(f"/admin/clients/{id_client}/remarques", headers=entete(jeton_admin))
    assert r.status_code == 200 and r.json()[0]["texte"] == "Quai de déchargement à gauche"
    export = client.get(f"/admin/clients/{id_client}/export", headers=entete(jeton_admin)).json()
    assert export["remarques"][0]["texte"] == "Quai de déchargement à gauche"
    # Un livreur n'a pas accès à la liste complète de l'administrateur
    assert client.get(f"/admin/clients/{id_client}/remarques", headers=entete(jeton_a)).status_code == 403

    db.delete(db.get(Livraison, dernier["id"]))           # plus de livraison prévue pour ce client
    db.commit()
    r = client.post(f"/admin/clients/{id_client}/anonymiser", json={"confirmation": id_client},
                    headers=entete(jeton_admin))
    assert r.status_code == 200, r.text
    assert db.scalar(select(RemarqueClient.id).where(RemarqueClient.client_id == id_client)) is None
