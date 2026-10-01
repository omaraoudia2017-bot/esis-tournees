"""Modèle prédictif du temps de service (temps passé chez un client), cahier des charges v6, module 5.

Démarrage à froid : valeurs fixes (10 min colis, 20 min palette).
Étape suivante : un Random Forest apprend le temps de service à partir des variables du client
(app/ml/variables.py), sur un historique simulé réaliste complété par les pointages réels.

L'historique réel étant encore très court, l'historique simulé suit des HYPOTHÈSES explicites
(dictionnaire HYPOTHESES ci-dessous), à confronter aux premiers pointages terrain. Le code
d'entraînement reste identique quand l'historique réel grandit : seule la proportion change.
"""
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
from sklearn.ensemble import RandomForestRegressor
from sklearn.feature_extraction import DictVectorizer
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from app.ml.variables import COLONNES

VERSION_MODELE = 1

# --------------------------------------------------------------------------- historique simulé
# Minutes ajoutées au temps de base. Ordres de grandeur à valider avec l'exploitation.
HYPOTHESES = {
    "base_colis": 5.0,
    "base_palette": 14.0,
    "par_etage_a_pied": 1.3,          # monter un étage avec le colis, sans ascenseur
    "par_etage_ascenseur": 0.3,       # attente et trajet en ascenseur
    "immeuble": 1.0,                  # hall, boîtes aux lettres, couloirs
    "sans_digicode_en_immeuble": 1.5, # attendre qu'un voisin ou le client ouvre
    "mention_interphone": 1.0,
    "mention_portail": 1.0,
    "mention_batiment": 1.5,          # trouver le bon bâtiment
    "mention_acces_difficile": 2.5,   # impasse, rue étroite, stationnement
    "mention_appel": 2.0,             # appel au client avant ou pendant la livraison
    "mention_patienter": 3.0,
    "mention_accueil": 2.0,           # passage par l'accueil d'une entreprise
    "mention_quai": -4.0,             # quai de déchargement : palette plus rapide
    "professionnel": 2.0,             # contrôle, signature du bon de livraison
    "zone_lyon": 1.0,                 # stationnement plus difficile en zone dense
    "bruit_log": 0.25,                # aléa multiplicatif (écart-type du logarithme)
    "minimum": 2.0,
}


def temps_theorique_min(v: dict[str, Any], h: dict[str, float] = HYPOTHESES) -> float:
    """Partie expliquée du temps de service simulé (sans aléa), en minutes."""
    t = h["base_palette"] if v["camion_hayon"] else h["base_colis"]
    t += v["etages_a_pied"] * h["par_etage_a_pied"]
    if v["ascenseur"] == "oui":
        t += v["etage"] * h["par_etage_ascenseur"]
    if v["immeuble"]:
        t += h["immeuble"]
        if not v["digicode_renseigne"] and not v["mention_digicode"]:
            t += h["sans_digicode_en_immeuble"]
    for nom in ("mention_interphone", "mention_portail", "mention_batiment", "mention_acces_difficile",
                "mention_appel", "mention_patienter", "mention_accueil"):
        if v[nom]:
            t += h[nom]
    if v["mention_quai"] and v["camion_hayon"]:
        t += h["mention_quai"]
    if v["professionnel"]:
        t += h["professionnel"]
    if v["zone"] == "Lyon - Villeurbanne":
        t += h["zone_lyon"]
    return max(t, h["minimum"])


def temps_simule_min(v: dict[str, Any], rng: random.Random, h: dict[str, float] = HYPOTHESES) -> float:
    """Temps « observé » simulé : partie expliquée × aléa log-normal (jamais négatif)."""
    return round(max(temps_theorique_min(v, h) * math.exp(rng.gauss(0, h["bruit_log"])),
                     h["minimum"]), 1)


def historique_simule(variables_clients: list[dict[str, Any]], nb_passages: int,
                      graine: int = 42) -> tuple[list[dict], list[float]]:
    """nb_passages livraisons tirées parmi les clients (un client peut être livré plusieurs fois)."""
    rng = random.Random(graine)
    X, y = [], []
    for _ in range(nb_passages):
        v = rng.choice(variables_clients)
        X.append(v)
        y.append(temps_simule_min(v, rng))
    return X, y


# --------------------------------------------------------------------------- référence et modèle
def temps_fixe_min(v: dict[str, Any]) -> float:
    """Valeurs théoriques du démarrage à froid, utilisées aujourd'hui par le solveur."""
    return 20.0 if v["camion_hayon"] else 10.0


def _selection(v: dict[str, Any]) -> dict[str, Any]:
    """Uniquement les colonnes déclarées ; booléens en 0 / 1 (le texte devient du one-hot)."""
    return {k: (int(v[k]) if isinstance(v[k], bool) else v[k]) for k in COLONNES}


def nouveau_modele(graine: int = 42, arbres: int = 200) -> Pipeline:
    return Pipeline([
        ("encodage", DictVectorizer(sparse=False)),   # code_postal=69003 -> colonne 0 / 1
        ("foret", RandomForestRegressor(n_estimators=arbres, min_samples_leaf=5,
                                        n_jobs=-1, random_state=graine)),
    ])


@dataclass
class Rapport:
    nb_entrainement: int
    nb_test: int
    mae_modele_min: float
    mae_fixe_min: float
    r2_modele: float
    importances: list[tuple[str, float]] = field(default_factory=list)

    @property
    def gain_pct(self) -> float:
        return round(100 * (1 - self.mae_modele_min / self.mae_fixe_min), 1) if self.mae_fixe_min else 0.0


def importances_groupees(modele: Pipeline) -> list[tuple[str, float]]:
    """Importance par variable d'origine (les colonnes one-hot 'zone=...' sont regroupées)."""
    noms = modele.named_steps["encodage"].get_feature_names_out()
    valeurs = modele.named_steps["foret"].feature_importances_
    total: dict[str, float] = {}
    for nom, valeur in zip(noms, valeurs):
        total[nom.split("=")[0]] = total.get(nom.split("=")[0], 0.0) + float(valeur)
    return sorted(((k, round(v, 4)) for k, v in total.items()), key=lambda kv: -kv[1])


def entrainer(X: list[dict], y: list[float], graine: int = 42, arbres: int = 200,
              part_test: float = 0.2) -> tuple[Pipeline, Rapport]:
    X = [_selection(v) for v in X]
    X_ent, X_test, y_ent, y_test = train_test_split(X, y, test_size=part_test, random_state=graine)
    modele = nouveau_modele(graine, arbres).fit(X_ent, y_ent)
    predits = modele.predict(X_test)
    fixes = [temps_fixe_min({"camion_hayon": bool(v["camion_hayon"])}) for v in X_test]
    rapport = Rapport(
        nb_entrainement=len(X_ent), nb_test=len(X_test),
        mae_modele_min=round(float(mean_absolute_error(y_test, predits)), 2),
        mae_fixe_min=round(float(mean_absolute_error(y_test, fixes)), 2),
        r2_modele=round(float(r2_score(y_test, predits)), 3),
        importances=importances_groupees(modele),
    )
    return modele, rapport


# --------------------------------------------------------------------------- sauvegarde et prédiction
def sauvegarder(modele: Pipeline, rapport: Rapport, chemin: str | Path, **infos) -> None:
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"version": VERSION_MODELE, "modele": modele, "rapport": rapport,
                 "colonnes": COLONNES, "entraine_le": datetime.now(timezone.utc).isoformat(),
                 **infos}, chemin)


class PredicteurTempsService:
    """Temps de service prévu, en secondes. Sans modèle entraîné (ou modèle incompatible),
    retombe sur les valeurs fixes : l'optimisation fonctionne toujours."""

    def __init__(self, chemin: str | Path | None = None):
        self.modele: Pipeline | None = None
        self.infos: dict[str, Any] = {}
        if chemin and Path(chemin).is_file():
            contenu = joblib.load(chemin)        # fichier produit par l'application elle-même
            if contenu.get("version") == VERSION_MODELE and contenu.get("colonnes") == COLONNES:
                self.modele = contenu["modele"]
                self.infos = {k: v for k, v in contenu.items() if k != "modele"}

    @property
    def actif(self) -> bool:
        return self.modele is not None

    def predire_s(self, variables: list[dict[str, Any]]) -> list[int]:
        if not variables:
            return []
        if self.modele is None:
            return [int(temps_fixe_min(v) * 60) for v in variables]
        minutes = self.modele.predict([_selection(v) for v in variables])
        return [int(round(max(m, HYPOTHESES["minimum"]) * 60)) for m in minutes]
