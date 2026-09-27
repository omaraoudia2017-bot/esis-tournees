"""Chiffrement symétrique Fernet (AES-128-CBC + HMAC-SHA256) des données sensibles."""
from functools import lru_cache

from cryptography.fernet import Fernet

from app.core.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    return Fernet(get_settings().fernet_key.encode())


def chiffrer(texte: str) -> str:
    return _fernet().encrypt(texte.encode("utf-8")).decode("ascii")


def dechiffrer(jeton: str) -> str:
    # Lève cryptography.fernet.InvalidToken si la donnée a été altérée ou si la clé est fausse
    return _fernet().decrypt(jeton.encode("ascii")).decode("utf-8")
