"""Tournées proposées « L » (L1, L2, L3…) : préparées à l'import d'un fichier de clients, sans date
ni livreur. Le planificateur les attribue ensuite (date, livreur, période, heure de départ) : une
vraie tournée est alors créée et la tournée L garde le lien vers elle (traçabilité).

Le code « L » + identifiant n'est jamais réutilisé.
"""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PERIODES_L = ("matin", "apres-midi", "soiree", "journee")


class TourneeL(Base):
    __tablename__ = "tournees_proposees"
    __table_args__ = (
        CheckConstraint("periode IN ('matin', 'apres-midi', 'soiree', 'journee')", name="tournee_l_periode"),
        CheckConstraint("type_camion IN ('normal', 'hayon')", name="tournee_l_camion"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    periode: Mapped[str] = mapped_column(String(12))
    type_camion: Mapped[str] = mapped_column(String(10))
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id", ondelete="SET NULL"))
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Renseignés à l'attribution : la tournée L sort alors de la liste « à attribuer »
    tournee_id: Mapped[int | None] = mapped_column(ForeignKey("tournees.id", ondelete="SET NULL"), index=True)
    attribuee_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def code(self) -> str:
        return f"L{self.id}"


class ClientTourneeL(Base):
    __tablename__ = "tournees_proposees_clients"
    __table_args__ = (UniqueConstraint("tournee_l_id", "client_id", name="uq_tournee_l_client"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tournee_l_id: Mapped[int] = mapped_column(ForeignKey("tournees_proposees.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id_client", ondelete="CASCADE"), index=True)
    ordre: Mapped[int] = mapped_column(SmallInteger, default=0)
    ajoute_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
