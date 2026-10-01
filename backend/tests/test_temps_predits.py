"""Réinjection des temps de service prédits dans la planification et l'écran du livreur."""
from datetime import time
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.ml.temps_service import entrainer, historique_simule, sauvegarder
from app.models.enums import TypeCamion
from app.services.planification import construire_probleme, estimer_temps_service_s
from tests.outils import connecter, entete
from tests.test_temps_service import clients_varies


def fiche(**champs):
    base = dict(id_client="8899000099", type_camion_requis=TypeCamion.NORMAL, type_client="particulier",
                code_postal="69003", latitude=45.76, longitude=4.85, est_immeuble=False, etage=None,
                ascenseur=None, digicode=None, telephone=None, creneau_debut=time(8),
                creneau_fin=time(12), notes_livraison=None)
    base.update(champs)
    return SimpleNamespace(**base)


@pytest.fixture
def modele_actif(tmp_path, monkeypatch):
    """Petit modèle entraîné dans un dossier temporaire et activé pour le test."""
    X, y = historique_simule(clients_varies(200), 1500, graine=4)
    modele, rapport = entrainer(X, y, graine=4, arbres=30)
    chemin = tmp_path / "temps_service.joblib"
    sauvegarder(modele, rapport, chemin)
    reglages = get_settings()
    monkeypatch.setattr(reglages, "modele_temps_service", str(chemin))
    monkeypatch.setattr(reglages, "utiliser_modele_temps_service", True)
    return chemin


def test_valeurs_fixes_sans_modele():
    temps, source = estimer_temps_service_s([fiche(), fiche(type_camion_requis=TypeCamion.HAYON)], get_settings())
    assert source == "fixe" and temps == [600, 1200]


def test_temps_predits_avec_modele(modele_actif):
    rdc = fiche()
    sixieme = fiche(est_immeuble=True, etage=6, ascenseur=False)
    temps, source = estimer_temps_service_s([rdc, sixieme], get_settings())
    assert source == "modele"
    assert temps[1] > temps[0] >= 120 and temps != [600, 600]
    p = construire_probleme([rdc, sixieme], get_settings(), 10)
    assert [c.temps_service_s for c in p.clients] == temps        # injectés dans le solveur


def test_modele_absent_ou_corrompu(tmp_path, monkeypatch):
    reglages = get_settings()
    monkeypatch.setattr(reglages, "utiliser_modele_temps_service", True)
    monkeypatch.setattr(reglages, "modele_temps_service", str(tmp_path / "absent.joblib"))
    assert estimer_temps_service_s([fiche()], reglages) == ([600], "fixe")
    illisible = tmp_path / "illisible.joblib"
    illisible.write_bytes(b"pas un modele")
    monkeypatch.setattr(reglages, "modele_temps_service", str(illisible))
    assert estimer_temps_service_s([fiche()], reglages) == ([600], "fixe")   # repli, pas de panne


def test_optimisation_utilise_le_modele(client, db, jeu, modele_actif):
    jeton = connecter(client, "test.livreur.a", jeu["secrets"]["a"])
    r = client.post("/tournees/du-jour/optimiser", headers=entete(jeton))
    assert r.status_code == 200 and r.json()["temps_service"] == "modele"
    livraisons = client.get("/tournees/du-jour", headers=entete(jeton)).json()["livraisons"]
    predits = [l["temps_service_predit_s"] for l in livraisons if l["statut"] == "a_livrer"]
    assert predits and all(p >= 120 for p in predits)
