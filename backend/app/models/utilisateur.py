"""Comptes applicatifs (livreurs et administrateurs)."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import EncryptedText
from app.models.enums import RoleUtilisateur, pg_enum


class Utilisateur(Base):
    __tablename__ = "utilisateurs"

    id: Mapped[int] = mapped_column(primary_key=True)
    identifiant: Mapped[str] = mapped_column(String(50), unique=True)
    nom: Mapped[str] = mapped_column(String(80))
    prenom: Mapped[str] = mapped_column(String(80))
    role: Mapped[RoleUtilisateur] = mapped_column(
        pg_enum(RoleUtilisateur, "role_utilisateur"), default=RoleUtilisateur.LIVREUR
    )

    # Authentification : hash bcrypt uniquement, jamais le mot de passe en clair
    mot_de_passe_hash: Mapped[str] = mapped_column(String(255))
    # 2FA TOTP : secret chiffré Fernet, activé après le premier scan du QR code
    totp_secret: Mapped[str | None] = mapped_column(EncryptedText)
    totp_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Protection contre la force brute
    tentatives_echec: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    verrouille_jusqua: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    vehicule_id: Mapped[int | None] = mapped_column(ForeignKey("vehicules.id", ondelete="SET NULL"))
    actif: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    derniere_connexion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    modifie_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
