"""Cache des résultats de géocodage Nominatim.

Une même adresse n'est jamais demandée deux fois au service (quota : 1 requête/seconde),
y compris quand elle est introuvable (precision = 'echec').
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import PRECISION_GEOCODAGE_PG, PrecisionGeocodage


class CacheGeocodage(Base):
    __tablename__ = "cache_geocodage"

    # Clé : "VOIE|CP|VILLE" normalisée
    cle: Mapped[str] = mapped_column(String(320), primary_key=True)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    precision: Mapped[PrecisionGeocodage] = mapped_column(
        PRECISION_GEOCODAGE_PG
    )
    libelle_osm: Mapped[str | None] = mapped_column(String(500))   # adresse renvoyée par OSM
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
