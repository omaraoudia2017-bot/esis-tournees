"""Comptes applicatifs (livreurs et administrateurs)."""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import EncryptedText
from app.models.enums import RoleUtilisateur, TypeLivreur, pg_enum


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
    # Compte créé ou mot de passe réinitialisé par le planificateur : le hash ci-dessus est celui
    # d'un CODE D'ACTIVATION à usage unique (valable jusqu'à activation_expire_le). À la connexion
    # avec ce code, l'utilisateur doit choisir son mot de passe avant toute autre étape.
    mdp_a_definir: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    activation_expire_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 2FA TOTP : secret chiffré Fernet, activé après le premier scan du QR code
    totp_secret: Mapped[str | None] = mapped_column(EncryptedText)
    totp_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Dernier pas de temps TOTP accepté : un code déjà utilisé ne peut pas être rejoué
    totp_dernier_pas: Mapped[int | None] = mapped_column(BigInteger)

    # Protection contre la force brute
    tentatives_echec: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    verrouille_jusqua: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Jetons émis avant cette date refusés (désactivation, réinitialisation du mot de passe ou 2FA)
    sessions_invalidees_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Livreur interne (rentre avec le camion) ou prestataire (rend le camion au dépôt). NULL = admin.
    type_livreur: Mapped[TypeLivreur | None] = mapped_column(pg_enum(TypeLivreur, "type_livreur"))
    # Livreur interne : adresse de départ et de retour de la tournée, saisie par le livreur lui-même.
    # Donnée personnelle sensible (domicile) : adresse ET position chiffrées (Fernet), jamais mises
    # dans le cache de géocodage partagé, jamais affichées à l'administrateur.
    domicile_adresse: Mapped[str | None] = mapped_column(EncryptedText)
    domicile_position: Mapped[str | None] = mapped_column(EncryptedText)    # "lat;lon" (4 décimales)
    domicile_maj_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Conditions d'utilisation et information RGPD : version lue et acceptée, et quand.
    # Une nouvelle version (CONDITIONS_VERSION) oblige le livreur à les relire et à les accepter.
    conditions_version: Mapped[str | None] = mapped_column(String(10))
    conditions_acceptees_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    vehicule_id: Mapped[int | None] = mapped_column(ForeignKey("vehicules.id", ondelete="SET NULL"))
    actif: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    derniere_connexion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    modifie_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CodeSecours(Base):
    """Codes de secours 2FA à usage unique (perte du téléphone). Seule l'empreinte est stockée."""
    __tablename__ = "codes_secours"

    id: Mapped[int] = mapped_column(primary_key=True)
    utilisateur_id: Mapped[int] = mapped_column(
        ForeignKey("utilisateurs.id", ondelete="CASCADE"), index=True
    )
    empreinte: Mapped[str] = mapped_column(String(64))       # HMAC-SHA256 du code
    utilise_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JetonRevoque(Base):
    """Jetons invalidés par une déconnexion explicite, conservés jusqu'à leur expiration."""
    __tablename__ = "jetons_revoques"

    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    expire_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
