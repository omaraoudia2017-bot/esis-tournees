"""Modèle de temps de service : historique simulé, entraînement, critère MAE, sauvegarde."""
import random
from datetime import time
from types import SimpleNamespace

from app.ml.temps_service import (HYPOTHESES, PredicteurTempsService, entrainer, historique_simule,
                                  sauvegarder, temps_fixe_min, temps_simule_min, temps_theorique_min)
from app.ml.variables import variables_client

DEPOT = (45.704747, 4.766921)


def client(**champs):
    base = dict(type_camion_requis="normal", type_client="particulier", code_postal="69003",
                latitude=45.76, longitude=4.85, est_immeuble=False, etage=None, ascenseur=None,
                digicode=None, telephone=None, creneau_debut=time(8), creneau_fin=time(10),
                notes_livraison=None)
    base.update(champs)
    return variables_client(SimpleNamespace(**base), DEPOT)


def clients_varies(n=400, graine=3):
    """Petit portefeuille de clients aux profils variés (sans base de données)."""
    rng = random.Random(graine)
    notes = [None, "Sonner a l interphone.", "Maison au fond de l impasse.", "BAT: B, portail vert.",
             "Appeler en arrivant.", "Livraison sur palette par le QUAI."]
    liste = []
    for _ in range(n):
        pro = rng.random() < 0.15
        immeuble = rng.random() < 0.6
        liste.append(client(
            type_camion_requis="hayon" if pro and rng.random() < 0.5 else "normal",
            type_client="professionnel" if pro else "particulier",
            est_immeuble=immeuble, etage=rng.randint(0, 6) if immeuble else None,
            ascenseur=rng.choice([True, False]) if immeuble else None,
            digicode="1234" if rng.random() < 0.3 else None, notes_livraison=rng.choice(notes),
            code_postal=rng.choice(["69003", "69100", "69130", "69630"]),
            latitude=45.76 + rng.uniform(-0.1, 0.1), longitude=4.85 + rng.uniform(-0.1, 0.1)))
    return liste


def test_hypotheses_dans_le_bon_sens():
    plain_pied = temps_theorique_min(client())
    cinquieme_sans_asc = temps_theorique_min(client(est_immeuble=True, etage=5, ascenseur=False,
                                                    digicode="1"))
    cinquieme_avec_asc = temps_theorique_min(client(est_immeuble=True, etage=5, ascenseur=True,
                                                    digicode="1"))
    assert plain_pied < cinquieme_avec_asc < cinquieme_sans_asc
    palette = temps_theorique_min(client(type_camion_requis="hayon"))
    palette_quai = temps_theorique_min(client(type_camion_requis="hayon",
                                              notes_livraison="Livraison par le QUAI"))
    assert palette_quai < palette and palette > plain_pied
    assert temps_theorique_min(client(notes_livraison="Maison au fond de l impasse")) > plain_pied


def test_alea_reproductible_et_jamais_sous_le_minimum():
    v = client()
    a = [temps_simule_min(v, random.Random(1)) for _ in range(3)]
    assert a[0] == a[1] == a[2]
    rng = random.Random(5)
    assert min(temps_simule_min(v, rng) for _ in range(2000)) >= HYPOTHESES["minimum"]
    X1, y1 = historique_simule(clients_varies(50), 200, graine=9)
    X2, y2 = historique_simule(clients_varies(50), 200, graine=9)
    assert y1 == y2 and len(X1) == 200


def test_modele_meilleur_que_les_valeurs_fixes():
    X, y = historique_simule(clients_varies(), 3000, graine=11)
    modele, rapport = entrainer(X, y, graine=11, arbres=60)
    assert rapport.nb_entrainement == 2400 and rapport.nb_test == 600
    assert rapport.mae_modele_min < rapport.mae_fixe_min          # critère du cahier des charges
    assert rapport.r2_modele > 0.5
    noms = [n for n, _ in rapport.importances]
    assert {"etages_a_pied", "etage"} & set(noms[:5])              # l étage pèse le plus
    assert abs(sum(p for _, p in rapport.importances) - 1) < 0.01
    assert all("=" not in n for n in noms)                        # one-hot regroupé par variable


def test_predicteur_sans_modele_utilise_les_valeurs_fixes(tmp_path):
    p = PredicteurTempsService(tmp_path / "absent.joblib")
    assert not p.actif
    assert p.predire_s([client(), client(type_camion_requis="hayon")]) == [600, 1200]
    assert temps_fixe_min(client(type_camion_requis="hayon")) == 20.0


def test_sauvegarde_puis_chargement(tmp_path):
    X, y = historique_simule(clients_varies(100), 600, graine=2)
    modele, rapport = entrainer(X, y, graine=2, arbres=20)
    chemin = tmp_path / "modeles" / "temps_service.joblib"
    sauvegarder(modele, rapport, chemin, graine=2)
    p = PredicteurTempsService(chemin)
    assert p.actif and p.infos["rapport"].mae_modele_min == rapport.mae_modele_min
    secondes = p.predire_s([client(), client(est_immeuble=True, etage=6, ascenseur=False)])
    assert all(s >= HYPOTHESES["minimum"] * 60 for s in secondes)
    assert secondes[1] > secondes[0]                              # 6e étage à pied plus long
    assert p.predire_s([]) == []
