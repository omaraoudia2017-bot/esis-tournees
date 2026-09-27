"""Référentiels : zones de sectorisation et flotte de véhicules."""
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import TYPE_CAMION_PG, TypeCamion


class Zone(Base):
    """Secteur géographique. max_clients = limite d'effectif (10 à 20 selon la densité)."""
    __tablename__ = "zones"
    __table_args__ = (
        CheckConstraint("max_clients BETWEEN 10 AND 20", name="max_clients_10_20"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    nom: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    max_clients: Mapped[int] = mapped_column(SmallInteger, default=15)


class ZoneCodePostal(Base):
    """Rattachement d'un code postal à une zone (affectation automatique des clients)."""
    __tablename__ = "zones_codes_postaux"
    __table_args__ = (
        CheckConstraint("code_postal ~ '^[0-9]{5}$'", name="code_postal_format"),
    )

    code_postal: Mapped[str] = mapped_column(String(5), primary_key=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id", ondelete="CASCADE"), index=True)


class Vehicule(Base):
    __tablename__ = "vehicules"

    id: Mapped[int] = mapped_column(primary_key=True)
    immatriculation: Mapped[str] = mapped_column(String(15), unique=True)
    type_camion: Mapped[TypeCamion] = mapped_column(TYPE_CAMION_PG)
    actif: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
