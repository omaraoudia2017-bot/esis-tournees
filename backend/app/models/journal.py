"""Journal de sécurité (traçabilité des connexions et actions sensibles)."""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JournalSecurite(Base):
    """Table en ajout seul : un trigger interdit toute modification (voir db/ddl.py)."""
    __tablename__ = "journal_securite"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    horodatage: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    utilisateur_id: Mapped[int | None] = mapped_column(
        # RESTRICT : un compte ayant des traces se désactive (actif=false), il ne se supprime pas
        ForeignKey("utilisateurs.id", ondelete="RESTRICT"), index=True
    )
    identifiant_saisi: Mapped[str | None] = mapped_column(String(50))  # utile si compte inconnu
    evenement: Mapped[str] = mapped_column(String(50), index=True)     # ex. CONNEXION_ECHEC
    succes: Mapped[bool] = mapped_column(Boolean)
    adresse_ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(255))
    details: Mapped[dict | None] = mapped_column(JSONB)
