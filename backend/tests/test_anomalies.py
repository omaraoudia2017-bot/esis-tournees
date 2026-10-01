"""Détection des connexions anormales : variables, modèle Isolation Forest, branchement à la connexion."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pyotp
from sqlalchemy import select

import app.services.anomalies as service
from app.ml.anomalies import (DetecteurAnomalies, Evenement, attaques_simulees, entrainer,
                              historique_simule, raisons, taux_alertes, variables_connexion)
from app.models import JournalSecurite
from tests.outils import connecter, creer_compte

PARIS = ZoneInfo("Europe/Paris")


def a(jour, heure, succes=True, ip="10.0.0.1", nav="nav-1", methode="totp"):
    return Evenement(datetime(2026, 3, jour, heure, 0, tzinfo=PARIS), succes, ip, nav, methode)


HABITUDES = [a(2, 7), a(3, 7), a(4, 6), a(5, 13), a(6, 7)]        # lundi -> vendredi


def test_connexion_habituelle():
    v = variables_connexion(a(9, 7), HABITUDES)                    # lundi 7 h, même IP et navigateur
    assert v["nuit"] == 0 and v["week_end"] == 0
    assert v["ip_nouvelle"] == 0 and v["navigateur_nouveau"] == 0 and v["echecs_24h"] == 0
    assert raisons(v) == ["combinaison inhabituelle"]


def test_connexion_suspecte():
    echecs = [a(8, 1, succes=False), a(8, 2, succes=False)]
    v = variables_connexion(a(8, 3, ip="203.0.113.9", nav="inconnu", methode="code_secours"),
                            HABITUDES + echecs)                   # dimanche 3 h
    assert v["nuit"] == 1 and v["week_end"] == 1 and v["echecs_24h"] == 2
    assert v["ip_nouvelle"] == 1 and v["navigateur_nouveau"] == 1 and v["code_secours"] == 1
    texte = " ".join(raisons(v))
    assert "nuit" in texte and "adresse IP" in texte and "2 échecs" in texte and "code de secours" in texte


def test_meme_reseau_n_est_pas_une_nouvelle_adresse():
    assert variables_connexion(a(9, 7, ip="10.0.0.42"), HABITUDES)["ip_nouvelle"] == 0
    assert variables_connexion(a(9, 7, ip="10.0.1.1"), HABITUDES)["ip_nouvelle"] == 1
    assert variables_connexion(a(9, 7, ip=None), HABITUDES)["ip_nouvelle"] == 1   # inconnue


def test_premiere_connexion_et_heure_circulaire():
    v = variables_connexion(a(9, 7, ip="1.2.3.4"), [])
    assert v["ip_nouvelle"] == 0 and v["navigateur_nouveau"] == 0      # rien à comparer
    h23 = variables_connexion(a(9, 23), HABITUDES)
    h1 = variables_connexion(a(10, 1), HABITUDES)
    assert abs(h23["heure_cos"] - h1["heure_cos"]) < 0.1                 # 23 h et 1 h sont voisines
    # seuls les événements ANTÉRIEURS comptent
    assert variables_connexion(a(2, 6), HABITUDES)["heures_depuis_derniere"] == 0.0


def test_isolation_forest_detecte_les_attaques_sans_trop_de_fausses_alertes():
    normales = historique_simule(nb_comptes=12, nb_jours=40, graine=1)
    apprentissage, controle = normales[::2], normales[1::2]
    modele = entrainer(apprentissage, contamination=0.02, graine=1)
    assert taux_alertes(modele, controle) < 0.10
    assert taux_alertes(modele, attaques_simulees(100, graine=2)) > 0.80


def test_reconnexion_apres_expiration_du_jeton_n_est_pas_une_alerte():
    """Jeton de 30 min : se reconnecter 40 min plus tard, même poste, est un usage normal."""
    normales = historique_simule(nb_comptes=12, nb_jours=40, graine=1)
    assert sum(v["connexions_1h"] >= 1 for v in normales) > 0.05 * len(normales)   # bien simulé
    modele = entrainer(normales, contamination=0.02, graine=1)
    matin = a(9, 7)
    reconnexion = Evenement(matin.horodatage + timedelta(minutes=40), True, "10.0.0.1", "nav-1", "totp")
    assert taux_alertes(modele, [variables_connexion(reconnexion, HABITUDES + [matin])]) == 0


def test_sans_modele_rien_n_est_signale(tmp_path):
    d = DetecteurAnomalies(tmp_path / "absent.joblib")
    assert not d.actif and d.analyser({}) == (False, 0.0)


class FauxDetecteur:
    def __init__(self, erreur=False):
        self.erreur = erreur

    def analyser(self, v):
        if self.erreur:
            raise RuntimeError("modèle illisible")
        return True, -0.12


def test_alerte_inscrite_au_journal_sans_bloquer_la_connexion(client, db, monkeypatch):
    secret = pyotp.random_base32()
    u = creer_compte(db, "test.anomalie", secret=secret)
    monkeypatch.setattr(service, "detecteur", lambda reglages: FauxDetecteur())
    connecter(client, "test.anomalie", secret)                        # 200 : connexion accordée
    alerte = db.scalar(select(JournalSecurite).where(JournalSecurite.utilisateur_id == u.id,
                                                     JournalSecurite.evenement == "CONNEXION_ANORMALE"))
    assert alerte is not None and not alerte.succes
    assert alerte.details["score"] == -0.12 and alerte.details["raisons"]


def test_erreur_du_modele_n_empeche_pas_la_connexion(client, db, monkeypatch):
    secret = pyotp.random_base32()
    u = creer_compte(db, "test.anomalie2", secret=secret)
    monkeypatch.setattr(service, "detecteur", lambda reglages: FauxDetecteur(erreur=True))
    connecter(client, "test.anomalie2", secret)
    evenements = db.scalars(select(JournalSecurite.evenement)
                            .where(JournalSecurite.utilisateur_id == u.id)).all()
    assert "CONNEXION_REUSSIE" in evenements and "CONNEXION_ANORMALE" not in evenements


def test_historique_du_compte_lu_dans_le_journal(db):
    u = creer_compte(db, "test.historique")
    maintenant = datetime.now(PARIS)
    for _ in range(3):
        service.journaliser(db, "CONNEXION_ECHEC", False, None, u.id, u.identifiant)
    db.commit()
    histo = service.historique_compte(db, u.id, maintenant + timedelta(minutes=1))
    assert len(histo) == 3 and not any(e.succes for e in histo)


def test_filtre_des_alertes_pour_l_administrateur(client, db, monkeypatch):
    from app.models.enums import RoleUtilisateur
    from tests.outils import entete
    s_admin, s_livreur = pyotp.random_base32(), pyotp.random_base32()
    creer_compte(db, "test.admin.alertes", RoleUtilisateur.ADMIN, s_admin)
    creer_compte(db, "test.livreur.alertes", secret=s_livreur)
    monkeypatch.setattr(service, "detecteur", lambda reglages: FauxDetecteur())
    connecter(client, "test.livreur.alertes", s_livreur)
    monkeypatch.setattr(service, "detecteur", lambda reglages: None)
    jeton = connecter(client, "test.admin.alertes", s_admin)
    r = client.get("/admin/journal-securite", params={"evenement": "CONNEXION_ANORMALE"},
                   headers=entete(jeton))
    assert r.status_code == 200 and r.json()
    assert {e["evenement"] for e in r.json()} == {"CONNEXION_ANORMALE"}
    assert any(e["identifiant_saisi"] == "test.livreur.alertes" for e in r.json())
    assert client.get("/admin/journal-securite", params={"evenement": "x' OR 1=1"},
                      headers=entete(jeton)).status_code == 422
