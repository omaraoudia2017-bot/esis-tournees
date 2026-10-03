"""Conditions d'utilisation et information RGPD du livreur (articles 12 à 14 du RGPD).

À la première connexion, puis à chaque nouvelle version du texte, le livreur doit :
  - accepter les conditions d'utilisation (règles d'usage du compte et des données clients) ;
  - attester avoir pris connaissance de l'information sur le traitement de ses données.
Il ne s'agit PAS d'un consentement au sens du RGPD : pour un salarié, le consentement n'est pas
considéré comme libre (CNIL). Les traitements reposent sur le contrat et l'intérêt légitime ;
l'acceptation prouve que l'information a été donnée (principe de responsabilité, article 5.2).

Tant que la version en vigueur n'est pas acceptée, les routes du livreur répondent 403
(contrôle dans app/api/deps.py) : l'acceptation ne peut pas être contournée par l'interface.
"""
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Utilisateur
from app.models.enums import RoleUtilisateur
from app.services.journal import ContexteRequete, journaliser

DOSSIER = Path(__file__).resolve().parent.parent / "documents"
MESSAGE_A_ACCEPTER = ("Vous devez lire et accepter les conditions d'utilisation avant d'accéder "
                      "à votre tournée.")


class ErreurConditions(Exception):
    def __init__(self, statut: int, message: str):
        super().__init__(message)
        self.statut, self.message = statut, message


@lru_cache
def texte_conditions(version: str) -> str:
    """Texte versionné, livré avec le code (jamais modifiable depuis l'application)."""
    chemin = DOSSIER / f"conditions_utilisation_v{version}.md"
    if not chemin.is_file():
        raise ErreurConditions(500, f"Texte des conditions v{version} introuvable.")
    return chemin.read_text(encoding="utf-8")


def doit_accepter(u: Utilisateur, reglages: Settings) -> bool:
    return u.role == RoleUtilisateur.LIVREUR and u.conditions_version != reglages.conditions_version


def accepter(db: Session, u: Utilisateur, version: str, reglages: Settings,
             ctx: ContexteRequete | None) -> Utilisateur:
    """La version envoyée doit être celle affichée ET celle en vigueur : on n'accepte jamais
    « à l'aveugle » un texte que l'utilisateur n'a pas vu."""
    if version != reglages.conditions_version:
        raise ErreurConditions(409, "Une nouvelle version des conditions est en vigueur : rechargez la page.")
    u.conditions_version = version
    u.conditions_acceptees_le = datetime.now(timezone.utc)
    journaliser(db, "CONDITIONS_ACCEPTEES", True, ctx, u.id, u.identifiant, version=version)
    db.commit()
    db.refresh(u)
    return u
