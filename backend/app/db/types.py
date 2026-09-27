"""Type de colonne chiffrée de façon transparente.

Écriture : le texte est chiffré (Fernet) avant l'INSERT/UPDATE.
Lecture  : il est déchiffré à la sortie de la base.
En base, seul le jeton chiffré est visible (même pour un administrateur PostgreSQL).
"""
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.core.crypto import chiffrer, dechiffrer


class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or value == "":
            return None
        return chiffrer(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return dechiffrer(value)
