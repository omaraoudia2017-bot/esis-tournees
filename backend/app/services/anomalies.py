"""Analyse de chaque connexion réussie par le modèle de détection d'anomalies (Isolation Forest).

Appelée à la fin de la connexion (après la 2FA). Une connexion anormale est inscrite dans le journal
de sécurité (« CONNEXION_ANORMALE », avec score et raisons lisibles) pour l'administrateur ;
elle n'est jamais bloquée, et une erreur d'analyse n'empêche jamais de se connecter.
"""
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.ml.anomalies import DetecteurAnomalies, Evenement, raisons, variables_connexion
from app.models import JournalSecurite, Utilisateur
from app.services.journal import ContexteRequete, journaliser

EVENEMENTS_UTILES = ("CONNEXION_REUSSIE", "CONNEXION_ECHEC")
PROFONDEUR = timedelta(days=90)

_detecteurs: dict[tuple[str, float], DetecteurAnomalies] = {}


def detecteur(reglages: Settings) -> DetecteurAnomalies | None:
    """Modèle chargé une fois, rechargé si le fichier change (nouvel entraînement)."""
    if not reglages.utiliser_detection_anomalies:
        return None
    chemin = Path(reglages.modele_anomalies)
    try:
        cle = (str(chemin), chemin.stat().st_mtime)
    except OSError:
        return None
    if cle not in _detecteurs:
        _detecteurs.clear()
        try:
            _detecteurs[cle] = DetecteurAnomalies(chemin)
        except Exception:      # fichier corrompu : pas d'analyse plutôt qu'une panne
            _detecteurs[cle] = DetecteurAnomalies(None)
    d = _detecteurs[cle]
    return d if d.actif else None


def _evenement(ligne: JournalSecurite) -> Evenement:
    return Evenement(ligne.horodatage, ligne.evenement == "CONNEXION_REUSSIE",
                     str(ligne.adresse_ip) if ligne.adresse_ip else None, ligne.user_agent,
                     (ligne.details or {}).get("methode"))


def historique_compte(db: Session, utilisateur_id: int, jusqua: datetime) -> list[Evenement]:
    lignes = db.scalars(select(JournalSecurite).where(
        JournalSecurite.utilisateur_id == utilisateur_id,
        JournalSecurite.evenement.in_(EVENEMENTS_UTILES),
        JournalSecurite.horodatage >= jusqua - PROFONDEUR,
        JournalSecurite.horodatage < jusqua,
    ).order_by(JournalSecurite.horodatage))
    return [_evenement(l) for l in lignes]


def connexions_reelles_detaillees(db: Session, fuseau: str) -> list[tuple[str, datetime, dict]]:
    """(compte, horodatage, variables) de chaque connexion réussie du journal."""
    lignes = db.scalars(select(JournalSecurite).where(
        JournalSecurite.evenement.in_(EVENEMENTS_UTILES), JournalSecurite.utilisateur_id.is_not(None)
    ).order_by(JournalSecurite.utilisateur_id, JournalSecurite.horodatage)).all()
    par_compte: dict[int, list[tuple[str, Evenement]]] = {}
    for l in lignes:
        par_compte.setdefault(l.utilisateur_id, []).append((l.identifiant_saisi or "?", _evenement(l)))
    resultat = []
    for evenements in par_compte.values():
        historique = [e for _, e in evenements]
        for i, (compte, e) in enumerate(evenements):
            if e.succes:
                resultat.append((compte, e.horodatage, variables_connexion(e, historique[:i], fuseau)))
    return resultat


def connexions_reelles(db: Session, fuseau: str) -> list[dict[str, float]]:
    """Variables de toutes les connexions réussies du journal (pour l'entraînement)."""
    return [v for _, _, v in connexions_reelles_detaillees(db, fuseau)]


def analyser_connexion(db: Session, u: Utilisateur, ctx: ContexteRequete, methode: str,
                       instant: datetime, reglages: Settings) -> bool:
    """True si une alerte a été inscrite au journal (l'appelant fait le commit)."""
    try:
        d = detecteur(reglages)
        if d is None:
            return False
        v = variables_connexion(Evenement(instant, True, ctx.adresse_ip, ctx.user_agent, methode),
                                historique_compte(db, u.id, instant), reglages.fuseau_horaire)
        anormale, score = d.analyser(v)
        if anormale:
            journaliser(db, "CONNEXION_ANORMALE", False, ctx, u.id, u.identifiant,
                        score=score, raisons=raisons(v))
        return anormale
    except Exception:       # la détection est une aide : elle ne doit jamais bloquer la connexion
        return False
