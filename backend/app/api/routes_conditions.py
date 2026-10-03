"""Conditions d'utilisation et information RGPD : lecture et acceptation par le livreur.

Ces routes restent accessibles tant que les conditions ne sont pas acceptées (elles n'utilisent
pas exiger_role, qui bloque le reste de l'application du livreur)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import utilisateur_courant
from app.core.config import get_settings
from app.db.session import get_db
from app.models import Utilisateur
from app.schemas.auth import ConditionsVue, DemandeAcceptation
from app.services import conditions as service
from app.services.journal import contexte_depuis

router = APIRouter(prefix="/conditions", tags=["Conditions d'utilisation"])


def _vue(u: Utilisateur) -> ConditionsVue:
    reglages = get_settings()
    try:
        texte = service.texte_conditions(reglages.conditions_version)
    except service.ErreurConditions as e:
        raise HTTPException(e.statut, e.message) from None
    return ConditionsVue(version=reglages.conditions_version, texte=texte,
                         a_accepter=service.doit_accepter(u, reglages),
                         version_acceptee=u.conditions_version, acceptee_le=u.conditions_acceptees_le)


@router.get("", response_model=ConditionsVue)
def lire(u: Utilisateur = Depends(utilisateur_courant)):
    """Texte en vigueur (relisible à tout moment) et état de l'acceptation du compte connecté."""
    return _vue(u)


@router.post("/accepter", response_model=ConditionsVue)
def accepter(demande: DemandeAcceptation, request: Request, u: Utilisateur = Depends(utilisateur_courant),
             db: Session = Depends(get_db)):
    """Acceptation datée et versionnée, inscrite au journal de sécurité (CONDITIONS_ACCEPTEES)."""
    try:
        u = service.accepter(db, u, demande.version, get_settings(), contexte_depuis(request))
    except service.ErreurConditions as e:
        raise HTTPException(e.statut, e.message) from None
    return _vue(u)
