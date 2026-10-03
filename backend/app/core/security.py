"""Primitives de sécurité : mots de passe (bcrypt), jetons (JWT), 2FA (TOTP), codes de secours.

Fonctions pures, sans accès à la base : testées dans tests/test_securite.py.
"""
import hashlib
import hmac
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
import pyotp

from app.core.config import get_settings

ALGORITHME_JWT = "HS256"
TYPE_ACCES = "acces"          # jeton de session (30 min)
TYPE_PREAUTH = "preauth"      # jeton intermédiaire : mot de passe OK, 2FA pas encore faite (5 min)

# --------------------------------------------------------------------------- mots de passe
LONGUEUR_MIN_MDP = 12
LONGUEUR_MAX_MDP_OCTETS = 72  # limite de l'algorithme bcrypt


def valider_politique_mdp(mdp: str) -> list[str]:
    """Retourne la liste des règles non respectées (vide si le mot de passe est conforme)."""
    erreurs = []
    if len(mdp) < LONGUEUR_MIN_MDP:
        erreurs.append(f"au moins {LONGUEUR_MIN_MDP} caractères")
    if len(mdp.encode("utf-8")) > LONGUEUR_MAX_MDP_OCTETS:
        erreurs.append(f"au plus {LONGUEUR_MAX_MDP_OCTETS} octets")
    if not any(c.islower() for c in mdp) or not any(c.isupper() for c in mdp):
        erreurs.append("des minuscules et des majuscules")
    if not any(c.isdigit() for c in mdp):
        erreurs.append("au moins un chiffre")
    return erreurs


def hacher_mdp(mdp: str) -> str:
    return bcrypt.hashpw(mdp.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verifier_mdp(mdp: str, hachage: str) -> bool:
    try:
        return bcrypt.checkpw(mdp.encode("utf-8"), hachage.encode("ascii"))
    except ValueError:        # hachage mal formé ou mot de passe trop long
        return False


# Hachage factice : on calcule quand même un bcrypt pour un identifiant inconnu,
# afin que le temps de réponse ne révèle pas si le compte existe (attaque par chronométrage).
HACHAGE_FACTICE = hacher_mdp("mot-de-passe-factice-ESIS-1")


# --------------------------------------------------------------------------- JWT
def creer_jeton(sujet: int, role: str, type_jeton: str, duree: timedelta) -> tuple[str, str, datetime]:
    """Retourne (jeton, jti, expiration). Le jti permet la révocation à la déconnexion."""
    maintenant = datetime.now(timezone.utc)
    expiration = maintenant + duree
    jti = str(uuid.uuid4())
    # « emis » : instant d'émission à la milliseconde (iat est arrondi à la seconde), comparé à
    # la date d'invalidation des sessions d'un compte (désactivation, réinitialisation)
    charge = {"sub": str(sujet), "role": role, "typ": type_jeton, "jti": jti,
              "iat": maintenant, "emis": round(maintenant.timestamp(), 3), "exp": expiration}
    jeton = jwt.encode(charge, get_settings().jwt_secret_key, algorithm=ALGORITHME_JWT)
    return jeton, jti, expiration


def decoder_jeton(jeton: str, type_attendu: str) -> dict:
    """Vérifie signature, expiration et type. Lève jwt.InvalidTokenError sinon."""
    charge = jwt.decode(jeton, get_settings().jwt_secret_key, algorithms=[ALGORITHME_JWT],
                        options={"require": ["exp", "iat", "sub", "jti", "typ"]})
    if charge.get("typ") != type_attendu:
        raise jwt.InvalidTokenError("type de jeton inattendu")
    return charge


# --------------------------------------------------------------------------- TOTP (2FA)
def nouveau_secret_totp() -> str:
    return pyotp.random_base32()


def uri_totp(secret: str, identifiant: str) -> str:
    """URI otpauth:// encodée dans le QR code scanné par Google/Microsoft Authenticator."""
    return pyotp.TOTP(secret).provisioning_uri(name=identifiant,
                                               issuer_name=get_settings().totp_emetteur)


def verifier_totp(secret: str, code: str, dernier_pas: int | None,
                  maintenant: datetime | None = None) -> int | None:
    """Vérifie un code à 6 chiffres (tolérance ±30 s).

    Retourne le pas de temps utilisé si le code est valide, None sinon.
    Un code déjà utilisé (pas <= dernier_pas) est refusé : protection contre le rejeu.
    """
    code = (code or "").strip().replace(" ", "")
    if not (len(code) == 6 and code.isdigit()):
        return None
    totp = pyotp.TOTP(secret)
    maintenant = maintenant or datetime.now(timezone.utc)
    pas_actuel = totp.timecode(maintenant)
    for decalage in (0, -1, 1):
        pas = pas_actuel + decalage
        if hmac.compare_digest(totp.at(maintenant, decalage), code):
            if dernier_pas is not None and pas <= dernier_pas:
                return None
            return pas
    return None


# --------------------------------------------------------------------------- codes de secours
ALPHABET_SECOURS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # sans 0/O ni 1/I pour éviter les confusions


def generer_codes_secours(nombre: int) -> list[str]:
    """Codes à usage unique, format XXXXX-XXXXX (~50 bits d'entropie chacun)."""
    codes = []
    for _ in range(nombre):
        brut = "".join(secrets.choice(ALPHABET_SECOURS) for _ in range(10))
        codes.append(f"{brut[:5]}-{brut[5:]}")
    return codes


def normaliser_code_secours(code: str) -> str:
    return "".join(c for c in (code or "").upper() if c.isalnum())


def hacher_code_secours(code: str) -> str:
    """HMAC-SHA256 avec la clé serveur : suffisant pour des codes aléatoires à forte entropie."""
    cle = get_settings().jwt_secret_key.encode("utf-8")
    return hmac.new(cle, normaliser_code_secours(code).encode("ascii"), hashlib.sha256).hexdigest()


# --------------------------------------------------------------------------- codes d'activation
def generer_code_activation() -> str:
    """Code remis par le planificateur à la création du compte (ou après réinitialisation du mot de
    passe) : format XXXX-XXXX, ~40 bits d'entropie, usage unique, durée limitée, et protégé par le
    verrouillage après 5 échecs. Stocké haché (bcrypt) comme un mot de passe."""
    brut = "".join(secrets.choice(ALPHABET_SECOURS) for _ in range(8))
    return f"{brut[:4]}-{brut[4:]}"


def normaliser_code_activation(code: str) -> str:
    """Majuscules, sans tiret ni espace : « k7m2 q9xa » = « K7M2-Q9XA »."""
    return normaliser_code_secours(code)[:16]


def generer_mdp(longueur: int = 16) -> str:
    """Mot de passe aléatoire conforme à la politique (création de compte, réinitialisation)."""
    alphabet = string.ascii_letters + string.digits + "-_!?*"
    while True:
        mdp = "".join(secrets.choice(alphabet) for _ in range(longueur))
        if not valider_politique_mdp(mdp):
            return mdp
