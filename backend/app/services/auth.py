"""Logique d'authentification (indépendante de HTTP) : 2 étapes, 2FA, verrouillage, sessions.

Messages volontairement génériques : ils ne révèlent jamais si un identifiant existe.
"""
import base64
import io
from datetime import datetime, timedelta, timezone

import qrcode
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import (HACHAGE_FACTICE, TYPE_ACCES, TYPE_PREAUTH, creer_jeton,
                               generer_codes_secours, hacher_code_secours, nouveau_secret_totp,
                               uri_totp, verifier_mdp, verifier_totp)
from app.models import CodeSecours, JetonRevoque, Utilisateur
from app.services.journal import ContexteRequete, journaliser

MESSAGE_IDENTIFIANTS = "Identifiant ou mot de passe incorrect."
MESSAGE_CODE = "Code de vérification incorrect ou expiré."


class ErreurAuth(Exception):
    def __init__(self, message: str, statut: int = 401):
        super().__init__(message)
        self.message, self.statut = message, statut


def _maintenant() -> datetime:
    return datetime.now(timezone.utc)


def est_verrouille(u: Utilisateur) -> bool:
    return u.verrouille_jusqua is not None and u.verrouille_jusqua > _maintenant()


def _enregistrer_echec(db: Session, u: Utilisateur, motif: str, ctx: ContexteRequete) -> None:
    reglages = get_settings()
    u.tentatives_echec += 1
    journaliser(db, "CONNEXION_ECHEC", False, ctx, u.id, u.identifiant,
                motif=motif, tentatives=u.tentatives_echec)
    if u.tentatives_echec >= reglages.max_echecs_connexion:
        u.verrouille_jusqua = _maintenant() + timedelta(minutes=reglages.verrouillage_minutes)
        u.tentatives_echec = 0          # nouveau compteur à la fin du verrouillage
        journaliser(db, "COMPTE_VERROUILLE", False, ctx, u.id, u.identifiant,
                    duree_minutes=reglages.verrouillage_minutes)
    db.commit()


# --------------------------------------------------------------------------- étape 1
def etape_mot_de_passe(db: Session, identifiant: str, mdp: str,
                       ctx: ContexteRequete) -> tuple[Utilisateur, str, str]:
    """Retourne (utilisateur, jeton de pré-authentification, étape suivante)."""
    ident = identifiant.strip().lower()
    u = db.scalar(select(Utilisateur).where(func.lower(Utilisateur.identifiant) == ident))

    refus = None
    if u is None:
        refus = "identifiant_inconnu"
    elif not u.actif:
        refus = "compte_inactif"
    elif est_verrouille(u):
        refus = "compte_verrouille"
    if refus:
        verifier_mdp(mdp, HACHAGE_FACTICE)   # même temps de réponse que pour un vrai compte
        journaliser(db, "CONNEXION_ECHEC", False, ctx, u.id if u else None, ident, motif=refus)
        db.commit()
        raise ErreurAuth(MESSAGE_IDENTIFIANTS)

    if not verifier_mdp(mdp, u.mot_de_passe_hash):
        _enregistrer_echec(db, u, "mot_de_passe", ctx)
        raise ErreurAuth(MESSAGE_IDENTIFIANTS)

    journaliser(db, "MOT_DE_PASSE_VALIDE", True, ctx, u.id, u.identifiant)
    db.commit()
    jeton, _, _ = creer_jeton(u.id, u.role.value, TYPE_PREAUTH,
                              timedelta(minutes=get_settings().preauth_expire_minutes))
    return u, jeton, ("verification" if u.totp_active else "enrolement")


def utilisateur_preauth(db: Session, charge: dict) -> Utilisateur:
    """Utilisateur d'un jeton de pré-authentification encore valable (non utilisé, non verrouillé)."""
    if db.get(JetonRevoque, charge["jti"]) is not None:
        raise ErreurAuth(MESSAGE_CODE)
    u = db.get(Utilisateur, int(charge["sub"]))
    if u is None or not u.actif or est_verrouille(u):
        raise ErreurAuth(MESSAGE_CODE)
    return u


# --------------------------------------------------------------------------- étape 2
def qr_code_png_base64(contenu: str) -> str:
    image = qrcode.make(contenu)
    tampon = io.BytesIO()
    image.save(tampon, format="PNG")
    return base64.b64encode(tampon.getvalue()).decode("ascii")


def demarrer_enrolement(db: Session, u: Utilisateur) -> tuple[str, str]:
    """Première connexion : génère (ou réutilise) le secret TOTP. Retourne (uri, secret)."""
    if u.totp_active:
        raise ErreurAuth("La double authentification est déjà activée.", 409)
    if not u.totp_secret:
        u.totp_secret = nouveau_secret_totp()   # stocké chiffré (Fernet)
        db.commit()
    return uri_totp(u.totp_secret, u.identifiant), u.totp_secret


def activer_2fa(db: Session, u: Utilisateur, code: str, charge: dict,
                ctx: ContexteRequete) -> tuple[dict, list[str]]:
    if u.totp_active or not u.totp_secret:
        raise ErreurAuth("Enrôlement 2FA non démarré ou déjà terminé.", 409)
    pas = verifier_totp(u.totp_secret, code, u.totp_dernier_pas)
    if pas is None:
        _enregistrer_echec(db, u, "code_2fa_enrolement", ctx)
        raise ErreurAuth(MESSAGE_CODE)

    u.totp_active, u.totp_dernier_pas = True, pas
    db.execute(delete(CodeSecours).where(CodeSecours.utilisateur_id == u.id))
    codes = generer_codes_secours(get_settings().nb_codes_secours)
    db.add_all(CodeSecours(utilisateur_id=u.id, empreinte=hacher_code_secours(c)) for c in codes)
    journaliser(db, "2FA_ACTIVEE", True, ctx, u.id, u.identifiant)
    return _finaliser_connexion(db, u, charge, ctx, methode="totp"), codes


def verifier_second_facteur(db: Session, u: Utilisateur, code: str, charge: dict,
                            ctx: ContexteRequete) -> dict:
    if not u.totp_active:
        raise ErreurAuth("La double authentification n'est pas encore activée.", 409)

    pas = verifier_totp(u.totp_secret, code, u.totp_dernier_pas)
    if pas is not None:
        u.totp_dernier_pas = pas
        return _finaliser_connexion(db, u, charge, ctx, methode="totp")

    secours = db.scalar(select(CodeSecours).where(
        CodeSecours.utilisateur_id == u.id,
        CodeSecours.empreinte == hacher_code_secours(code),
        CodeSecours.utilise_le.is_(None),
    ))
    if secours is None:
        _enregistrer_echec(db, u, "code_2fa", ctx)
        raise ErreurAuth(MESSAGE_CODE)

    secours.utilise_le = _maintenant()
    restants = db.scalar(select(func.count()).where(
        CodeSecours.utilisateur_id == u.id, CodeSecours.utilise_le.is_(None))) - 1
    journaliser(db, "CODE_SECOURS_UTILISE", True, ctx, u.id, u.identifiant, restants=restants)
    return _finaliser_connexion(db, u, charge, ctx, methode="code_secours")


def _finaliser_connexion(db: Session, u: Utilisateur, charge_preauth: dict,
                         ctx: ContexteRequete, methode: str) -> dict:
    u.tentatives_echec, u.verrouille_jusqua = 0, None
    u.derniere_connexion = _maintenant()
    # Le jeton de pré-authentification est à usage unique
    db.add(JetonRevoque(jti=charge_preauth["jti"],
                        expire_le=datetime.fromtimestamp(charge_preauth["exp"], tz=timezone.utc)))
    jeton, _, expiration = creer_jeton(u.id, u.role.value, TYPE_ACCES,
                                       timedelta(minutes=get_settings().jwt_expire_minutes))
    journaliser(db, "CONNEXION_REUSSIE", True, ctx, u.id, u.identifiant, methode=methode)
    db.commit()
    return {"access_token": jeton, "token_type": "bearer", "expire_le": expiration,
            "role": u.role.value}


# --------------------------------------------------------------------------- session
def deconnecter(db: Session, u: Utilisateur, charge: dict, ctx: ContexteRequete) -> None:
    db.add(JetonRevoque(jti=charge["jti"],
                        expire_le=datetime.fromtimestamp(charge["exp"], tz=timezone.utc)))
    db.execute(delete(JetonRevoque).where(JetonRevoque.expire_le < _maintenant()))  # ménage
    journaliser(db, "DECONNEXION", True, ctx, u.id, u.identifiant)
    db.commit()
