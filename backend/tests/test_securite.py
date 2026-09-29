"""Tests unitaires des primitives de sécurité (sans base de données)."""
from datetime import datetime, timedelta, timezone

import jwt
import pyotp
import pytest

from app.core.security import (TYPE_ACCES, TYPE_PREAUTH, creer_jeton, decoder_jeton,
                               generer_codes_secours, hacher_code_secours, hacher_mdp,
                               valider_politique_mdp, verifier_mdp, verifier_totp)


@pytest.mark.parametrize("mdp, conforme", [
    ("Court1", False),
    ("toutenminuscule1", False),
    ("SansChiffreDuTout", False),
    ("MotDePasse2026Ok", True),
])
def test_politique_mdp(mdp, conforme):
    assert (valider_politique_mdp(mdp) == []) is conforme


def test_bcrypt():
    h = hacher_mdp("MotDePasse2026Ok")
    assert h.startswith("$2b$12$") and "MotDePasse" not in h
    assert verifier_mdp("MotDePasse2026Ok", h)
    assert not verifier_mdp("motdepasse2026ok", h)
    assert not verifier_mdp("x", "hachage-invalide")


def test_jeton_aller_retour():
    jeton, jti, _ = creer_jeton(7, "livreur", TYPE_ACCES, timedelta(minutes=30))
    charge = decoder_jeton(jeton, TYPE_ACCES)
    assert charge["sub"] == "7" and charge["role"] == "livreur" and charge["jti"] == jti


def test_jeton_expire():
    jeton, _, _ = creer_jeton(7, "livreur", TYPE_ACCES, timedelta(seconds=-1))
    with pytest.raises(jwt.ExpiredSignatureError):
        decoder_jeton(jeton, TYPE_ACCES)


def test_jeton_preauth_refuse_comme_session():
    jeton, _, _ = creer_jeton(7, "livreur", TYPE_PREAUTH, timedelta(minutes=5))
    with pytest.raises(jwt.InvalidTokenError):
        decoder_jeton(jeton, TYPE_ACCES)


def test_jeton_falsifie():
    jeton, _, _ = creer_jeton(7, "livreur", TYPE_ACCES, timedelta(minutes=30))
    entete, charge, signature = jeton.split(".")
    faux = jwt.encode({"sub": "1", "role": "admin"}, "mauvaise-cle", algorithm="HS256")
    with pytest.raises(jwt.InvalidTokenError):
        decoder_jeton(f"{entete}.{faux.split('.')[1]}.{signature}", TYPE_ACCES)


def test_totp_valide_puis_rejeu_refuse():
    secret = pyotp.random_base32()
    t = datetime(2026, 9, 30, 8, 0, 10, tzinfo=timezone.utc)
    code = pyotp.TOTP(secret).at(t)
    pas = verifier_totp(secret, code, None, t)
    assert pas is not None
    assert verifier_totp(secret, code, pas, t) is None          # même code réutilisé


def test_totp_tolerance_30_secondes():
    secret = pyotp.random_base32()
    t = datetime(2026, 9, 30, 8, 0, 10, tzinfo=timezone.utc)
    code_precedent = pyotp.TOTP(secret).at(t - timedelta(seconds=30))
    assert verifier_totp(secret, code_precedent, None, t) is not None
    code_ancien = pyotp.TOTP(secret).at(t - timedelta(seconds=120))
    assert verifier_totp(secret, code_ancien, None, t) is None


@pytest.mark.parametrize("code", ["", "12345", "abcdef", "1234567"])
def test_totp_format_invalide(code):
    assert verifier_totp(pyotp.random_base32(), code, None) is None


def test_codes_secours():
    codes = generer_codes_secours(10)
    assert len(set(codes)) == 10 and all(len(c) == 11 and c[5] == "-" for c in codes)
    # la saisie est tolérante (minuscules, sans tiret)
    assert hacher_code_secours(codes[0]) == hacher_code_secours(codes[0].lower().replace("-", ""))
