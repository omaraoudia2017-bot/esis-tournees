"""Détection des connexions anormales par Isolation Forest (cahier des charges v6, module 5).

Principe : chaque connexion réussie est décrite par quelques variables tirées du journal de sécurité
(heure, jour, échecs récents, adresse IP et navigateur déjà vus ou non, rythme des connexions).
Un Isolation Forest, entraîné sur des connexions « normales », isole les points atypiques.
Une connexion jugée anormale n'est PAS bloquée : elle est inscrite comme alerte dans le journal
et signalée à l'administrateur. Le blocage reste le rôle des règles (verrouillage après 5 échecs).

Le journal réel étant encore court, l'entraînement est complété par des connexions simulées
réalistes (livreurs le matin et en début d'après-midi, planificateur aux heures de bureau).
Aucune adresse IP ni aucun navigateur n'entre dans les variables : seulement « déjà vu ou non ».
"""
import ipaddress
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import joblib
from sklearn.ensemble import IsolationForest

VERSION_MODELE = 1
COLONNES = ["heure_sin", "heure_cos", "week_end", "nuit", "echecs_24h", "ip_nouvelle",
            "navigateur_nouveau", "heures_depuis_derniere", "connexions_1h", "code_secours"]


@dataclass
class Evenement:
    """Une ligne utile du journal de sécurité (connexion réussie ou échec) pour UN compte."""
    horodatage: datetime
    succes: bool
    adresse_ip: str | None = None
    navigateur: str | None = None
    methode: str | None = None          # "totp" ou "code_secours" pour une connexion réussie


# --------------------------------------------------------------------------- variables
def reseau(ip: str | None) -> str | None:
    """Réseau d'une adresse (/24 en IPv4, /64 en IPv6) : une box ou un conteneur qui change
    d'adresse dans le même réseau n'est pas une « nouvelle adresse »."""
    try:
        adresse = ipaddress.ip_address(ip or "")
    except ValueError:
        return None
    prefixe = 24 if adresse.version == 4 else 64
    return str(ipaddress.ip_network(f"{adresse}/{prefixe}", strict=False))


def variables_connexion(connexion: Evenement, historique: list[Evenement],
                        fuseau: str = "Europe/Paris") -> dict[str, float]:
    """Variables d'une connexion réussie, à partir des événements ANTÉRIEURS du même compte."""
    local = connexion.horodatage.astimezone(ZoneInfo(fuseau))
    heure = local.hour + local.minute / 60
    avant = [e for e in historique if e.horodatage < connexion.horodatage]
    reussies = [e for e in avant if e.succes]
    premiere = not reussies          # première connexion du compte (enrôlement) : rien à comparer
    derniere = max((e.horodatage for e in reussies), default=None)
    ecart_h = (connexion.horodatage - derniere).total_seconds() / 3600 if derniere else 0.0
    return {
        "heure_sin": round(math.sin(2 * math.pi * heure / 24), 4),   # 23 h et 1 h sont proches
        "heure_cos": round(math.cos(2 * math.pi * heure / 24), 4),
        "week_end": float(local.weekday() >= 5),
        "nuit": float(heure < 5 or heure >= 22),
        "echecs_24h": float(sum(1 for e in avant if not e.succes
                                and connexion.horodatage - e.horodatage <= timedelta(hours=24))),
        "ip_nouvelle": 0.0 if premiere else float(reseau(connexion.adresse_ip) not in
                                                   {reseau(e.adresse_ip) for e in reussies}),
        "navigateur_nouveau": 0.0 if premiere else float(connexion.navigateur not in
                                                          {e.navigateur for e in reussies}),
        "heures_depuis_derniere": round(math.log1p(min(ecart_h, 24 * 60)), 4),
        "connexions_1h": float(sum(1 for e in reussies
                                   if connexion.horodatage - e.horodatage <= timedelta(hours=1))),
        "code_secours": float(connexion.methode == "code_secours"),
    }


def raisons(v: dict[str, float]) -> list[str]:
    """Explication lisible pour l'administrateur (le modèle seul ne dit pas « pourquoi »)."""
    r = []
    if v["nuit"]:
        r.append("heure inhabituelle (nuit)")
    if v["week_end"]:
        r.append("connexion le week-end")
    if v["ip_nouvelle"]:
        r.append("réseau (adresse IP) jamais utilisé par ce compte")
    if v["navigateur_nouveau"]:
        r.append("navigateur jamais utilisé par ce compte")
    if v["echecs_24h"] >= 2:
        r.append(f"{int(v['echecs_24h'])} échecs dans les 24 h précédentes")
    if v["connexions_1h"] >= 3:
        r.append("connexions très rapprochées")
    if v["code_secours"]:
        r.append("connexion avec un code de secours")
    return r or ["combinaison inhabituelle"]


# --------------------------------------------------------------------------- historique simulé
PROFILS = {
    # heures de connexion habituelles (moyenne, écart-type) et proportion
    "livreur": [((6.75, 0.5), 0.75), ((13.0, 0.4), 0.25)],
    "admin": [((8.5, 0.7), 0.45), ((13.5, 0.6), 0.3), ((17.5, 1.2), 0.25)],
}
# Le jeton expire après 30 min : on se reconnecte souvent dans la foulée (probabilité d'une
# reconnexion supplémentaire, répétée tant que le tirage réussit).
RECONNEXION = {"livreur": 0.35, "admin": 0.6}


def historique_simule(nb_comptes: int = 30, nb_jours: int = 60, graine: int = 42,
                      fuseau: str = "Europe/Paris") -> list[dict[str, float]]:
    """Connexions normales simulées, déjà transformées en variables."""
    rng = random.Random(graine)  # tirage de simulation reproductible (graine), sans usage de sécurité  # nosec B311
    tz = ZoneInfo(fuseau)
    origine = datetime(2026, 1, 5, tzinfo=tz)              # un lundi
    lignes = []
    for n in range(nb_comptes):
        role = "admin" if n % 10 == 0 else "livreur"
        ips = [f"10.0.{n}.{i}" for i in range(rng.randint(1, 3))]      # domicile, mobile...
        navigateurs = [f"nav-{n}-{i}" for i in range(rng.randint(1, 2))]
        evenements: list[Evenement] = []
        for jour in range(nb_jours):
            date = origine + timedelta(days=jour)
            if date.weekday() >= 5 and rng.random() > 0.05:          # rarement le week-end
                continue
            if rng.random() < 0.1:                                    # congé, absence
                continue
            heures = []
            for (moyenne, ecart), part in PROFILS[role]:
                if rng.random() > part + 0.2:
                    continue
                heure = min(max(rng.gauss(moyenne, ecart), 5.5), 21.5)
                heures.append(heure)
                while rng.random() < RECONNEXION[role]:               # session expirée
                    heure += rng.uniform(0.5, 2.0)
                    if heure > 21.5:
                        break
                    heures.append(heure)
            for heure in sorted(heures):
                t = date + timedelta(hours=heure)
                if rng.random() < 0.05:                               # faute(s) de frappe
                    for k in range(1 if rng.random() < 0.8 else 2):
                        evenements.append(Evenement(t - timedelta(minutes=1 + k), False))
                if rng.random() < 0.02:                               # navigateur mis à jour
                    navigateurs.append(f"nav-{n}-{len(navigateurs)}")
                    navigateur = navigateurs[-1]
                else:
                    navigateur = rng.choice(navigateurs)
                # nouvelle IP de temps en temps (changement de box, 4G) : comportement normal
                ip = rng.choice(ips) if rng.random() > 0.03 else f"10.9.{n}.{rng.randint(0, 255)}"
                connexion = Evenement(t, True, ip, navigateur, "totp")
                lignes.append(variables_connexion(connexion, evenements, fuseau))
                evenements.append(connexion)
    return lignes


def attaques_simulees(nb: int = 200, graine: int = 7) -> list[dict[str, float]]:
    """Connexions suspectes typiques, pour mesurer le taux de détection (jamais pour entraîner)."""
    rng = random.Random(graine)  # tirage de simulation reproductible (graine), sans usage de sécurité  # nosec B311
    lignes = []
    for _ in range(nb):
        heure = rng.choice([rng.uniform(0, 4.5), rng.uniform(22, 24)])
        lignes.append({
            "heure_sin": round(math.sin(2 * math.pi * heure / 24), 4),
            "heure_cos": round(math.cos(2 * math.pi * heure / 24), 4),
            "week_end": float(rng.random() < 0.3), "nuit": 1.0,
            "echecs_24h": float(rng.randint(1, 4)), "ip_nouvelle": 1.0,
            "navigateur_nouveau": float(rng.random() < 0.8),
            "heures_depuis_derniere": round(math.log1p(rng.uniform(1, 72)), 4),
            "connexions_1h": float(rng.randint(0, 2)),
            "code_secours": float(rng.random() < 0.3),
        })
    return lignes


# --------------------------------------------------------------------------- modèle
def _matrice(lignes: list[dict[str, float]]) -> list[list[float]]:
    return [[ligne[c] for c in COLONNES] for ligne in lignes]


def entrainer(normales: list[dict[str, float]], contamination: float = 0.02,
              graine: int = 42) -> IsolationForest:
    return IsolationForest(n_estimators=200, contamination=contamination,
                           random_state=graine).fit(_matrice(normales))


def taux_alertes(modele: IsolationForest, lignes: list[dict[str, float]]) -> float:
    if not lignes:
        return 0.0
    return sum(p == -1 for p in modele.predict(_matrice(lignes))) / len(lignes)


def sauvegarder(modele: IsolationForest, chemin: str | Path, **infos) -> None:
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"version": VERSION_MODELE, "colonnes": COLONNES, "modele": modele,
                 "entraine_le": datetime.now(timezone.utc).isoformat(), **infos}, chemin)


class DetecteurAnomalies:
    """Sans modèle entraîné, ne signale rien (les règles de verrouillage restent actives)."""

    def __init__(self, chemin: str | Path | None = None):
        self.modele: IsolationForest | None = None
        if chemin and Path(chemin).is_file():
            contenu = joblib.load(chemin)        # fichier produit par l'application elle-même
            if contenu.get("version") == VERSION_MODELE and contenu.get("colonnes") == COLONNES:
                self.modele = contenu["modele"]

    @property
    def actif(self) -> bool:
        return self.modele is not None

    @staticmethod
    def colonnes() -> list[str]:
        return COLONNES

    def analyser(self, v: dict[str, float]) -> tuple[bool, float]:
        """(anormale ?, score). Score négatif = d'autant plus atypique."""
        if self.modele is None:
            return False, 0.0
        x = _matrice([v])
        return bool(self.modele.predict(x)[0] == -1), round(float(self.modele.decision_function(x)[0]), 4)
