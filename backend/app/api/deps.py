"""Dépendances FastAPI : utilisateur authentifié, contrôle des rôles, limitation de débit."""
from collections.abc import Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.limiteur import LimiteurRequetes
from app.core.security import TYPE_ACCES, TYPE_PREAUTH, decoder_jeton
from app.db.session import get_db
from app.models import JetonRevoque, Utilisateur
from app.models.enums import RoleUtilisateur
from app.services.auth import ErreurAuth, utilisateur_preauth
from app.services.conditions import MESSAGE_A_ACCEPTER, doit_accepter
from app.services.journal import ContexteRequete, contexte_depuis, journaliser

_bearer = HTTPBearer(auto_error=False)
limiteur_auth = LimiteurRequetes(get_settings().limite_requetes_auth_par_minute)
limiteur_ip = LimiteurRequetes(6 * get_settings().limite_requetes_auth_par_minute)

NON_AUTHENTIFIE = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Session invalide ou expirée. Veuillez vous reconnecter.",
    headers={"WWW-Authenticate": "Bearer"},
)


def contexte(request: Request) -> ContexteRequete:
    return contexte_depuis(request)


def verifier_limite(db: Session, ctx: ContexteRequete, cle: str) -> None:
    """Deux compteurs : par compte visé (force brute) et par adresse IP (attaque sur plusieurs comptes)."""
    if not (limiteur_auth.autoriser(cle) and limiteur_ip.autoriser(f"ip:{ctx.adresse_ip}")):
        journaliser(db, "LIMITE_REQUETES", False, ctx, cle=cle)
        db.commit()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Trop de tentatives. Patientez une minute avant de réessayer.")


def charge_preauth(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    if creds is None:
        raise NON_AUTHENTIFIE
    try:
        return decoder_jeton(creds.credentials, TYPE_PREAUTH)
    except jwt.InvalidTokenError:
        raise NON_AUTHENTIFIE from None


def utilisateur_en_preauth(charge: dict = Depends(charge_preauth),
                           db: Session = Depends(get_db)) -> Utilisateur:
    try:
        return utilisateur_preauth(db, charge)
    except ErreurAuth:
        raise NON_AUTHENTIFIE from None


def charge_acces(creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
                 db: Session = Depends(get_db)) -> dict:
    if creds is None:
        raise NON_AUTHENTIFIE
    try:
        charge = decoder_jeton(creds.credentials, TYPE_ACCES)
    except jwt.InvalidTokenError:
        raise NON_AUTHENTIFIE from None
    if db.get(JetonRevoque, charge["jti"]) is not None:     # jeton déconnecté
        raise NON_AUTHENTIFIE
    return charge


def utilisateur_courant(charge: dict = Depends(charge_acces),
                        db: Session = Depends(get_db)) -> Utilisateur:
    u = db.get(Utilisateur, int(charge["sub"]))
    if u is None or not u.actif:
        raise NON_AUTHENTIFIE
    # Mot de passe ou 2FA réinitialisé par l'administrateur : les sessions ouvertes avant tombent
    emis = charge.get("emis", charge["iat"])
    if u.sessions_invalidees_le and emis < u.sessions_invalidees_le.timestamp():
        raise NON_AUTHENTIFIE
    return u


def exiger_role(*roles: RoleUtilisateur) -> Callable:
    """Contrôle d'accès par rôle, vérifié côté serveur à chaque requête (RBAC).
    Un livreur doit aussi avoir accepté la version en vigueur des conditions d'utilisation."""
    def verification(request: Request, u: Utilisateur = Depends(utilisateur_courant),
                     db: Session = Depends(get_db)) -> Utilisateur:
        if u.role not in roles:
            journaliser(db, "ACCES_REFUSE", False, contexte_depuis(request), u.id, u.identifiant,
                        route=request.url.path, role=u.role.value)
            db.commit()
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Accès refusé.")
        # Livreur : conditions d'utilisation et information RGPD acceptées (version en vigueur)
        if doit_accepter(u, get_settings()):
            raise HTTPException(status.HTTP_403_FORBIDDEN, MESSAGE_A_ACCEPTER)
        return u
    return verification
