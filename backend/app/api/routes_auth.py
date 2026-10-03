"""Routes d'authentification : connexion en deux étapes, 2FA, déconnexion, profil."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import (charge_acces, charge_preauth, contexte, utilisateur_courant,
                          utilisateur_en_preauth, verifier_limite)
from app.core.config import get_settings
from app.db.session import get_db
from app.models import Utilisateur
from app.schemas.auth import (DemandeCode, DemandeConnexion, ProfilUtilisateur, ReponseActivation,
                              ReponseEnrolement, ReponseEtape1, ReponseJeton)
from app.services import auth as service
from app.services.conditions import doit_accepter
from app.services.journal import ContexteRequete

router = APIRouter(prefix="/auth", tags=["Authentification"])


def _http(erreur: service.ErreurAuth) -> HTTPException:
    entetes = {"WWW-Authenticate": "Bearer"} if erreur.statut == 401 else None
    return HTTPException(erreur.statut, erreur.message, headers=entetes)


@router.post("/connexion", response_model=ReponseEtape1)
def connexion(demande: DemandeConnexion, db: Session = Depends(get_db),
              ctx: ContexteRequete = Depends(contexte)):
    """Étape 1 : identifiant + mot de passe. Renvoie un jeton temporaire pour l'étape 2FA."""
    verifier_limite(db, ctx, f"connexion:{demande.identifiant.strip().lower()}")
    try:
        _, jeton, etape = service.etape_mot_de_passe(db, demande.identifiant, demande.mot_de_passe, ctx)
    except service.ErreurAuth as e:
        raise _http(e) from None
    return ReponseEtape1(etape=etape, jeton_preauth=jeton,
                         expire_dans_s=60 * get_settings().preauth_expire_minutes)


@router.post("/2fa/enrolement", response_model=ReponseEnrolement)
def enrolement(u: Utilisateur = Depends(utilisateur_en_preauth), db: Session = Depends(get_db)):
    """Première connexion : QR code à scanner avec Google / Microsoft Authenticator."""
    try:
        uri, secret = service.demarrer_enrolement(db, u)
    except service.ErreurAuth as e:
        raise _http(e) from None
    return ReponseEnrolement(uri_otpauth=uri, qr_code_png_base64=service.qr_code_png_base64(uri),
                             cle_manuelle=secret)


@router.post("/2fa/activation", response_model=ReponseActivation)
def activation(demande: DemandeCode, u: Utilisateur = Depends(utilisateur_en_preauth),
               charge: dict = Depends(charge_preauth), db: Session = Depends(get_db),
               ctx: ContexteRequete = Depends(contexte)):
    """Valide le premier code, active la 2FA, renvoie la session et les codes de secours."""
    verifier_limite(db, ctx, f"2fa:{u.id}")
    try:
        jeton, codes = service.activer_2fa(db, u, demande.code, charge, ctx)
    except service.ErreurAuth as e:
        raise _http(e) from None
    return ReponseActivation(**jeton, codes_secours=codes)


@router.post("/2fa/verification", response_model=ReponseJeton)
def verification(demande: DemandeCode, u: Utilisateur = Depends(utilisateur_en_preauth),
                 charge: dict = Depends(charge_preauth), db: Session = Depends(get_db),
                 ctx: ContexteRequete = Depends(contexte)):
    """Étape 2 : code TOTP à 6 chiffres (ou code de secours). Renvoie le jeton de session 30 min."""
    verifier_limite(db, ctx, f"2fa:{u.id}")
    try:
        return service.verifier_second_facteur(db, u, demande.code, charge, ctx)
    except service.ErreurAuth as e:
        raise _http(e) from None


@router.post("/deconnexion", status_code=status.HTTP_204_NO_CONTENT)
def deconnexion(u: Utilisateur = Depends(utilisateur_courant), charge: dict = Depends(charge_acces),
                db: Session = Depends(get_db), ctx: ContexteRequete = Depends(contexte)):
    """Révoque immédiatement le jeton (il ne pourra plus être utilisé, même avant expiration)."""
    service.deconnecter(db, u, charge, ctx)


@router.get("/moi", response_model=ProfilUtilisateur)
def moi(u: Utilisateur = Depends(utilisateur_courant)):
    return ProfilUtilisateur(id=u.id, identifiant=u.identifiant, nom=u.nom, prenom=u.prenom,
                             role=u.role.value, totp_active=u.totp_active,
                             derniere_connexion=u.derniere_connexion,
                             conditions_a_accepter=doit_accepter(u, get_settings()),
                             conditions_version=u.conditions_version,
                             conditions_acceptees_le=u.conditions_acceptees_le)
