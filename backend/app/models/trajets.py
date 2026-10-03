"""Journée de trajets : les jours sans tournée, le livreur organise lui-même ses déplacements de
service (garage, contrôle technique, récupération d'une pièce…) et les fait optimiser.

Ces étapes ne sont PAS des clients : elles ont leurs propres tables, pour ne fausser ni le tableau
de bord, ni l'entraînement du modèle de temps de service, ni les droits RGPD des clients.
Libellé, adresse et position sont chiffrés (une étape peut révéler une information personnelle).
"""
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, SmallInteger,
                        String, Time, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import EncryptedText


class JourneeTrajets(Base):
    __tablename__ = "journees_trajets"
    __table_args__ = (
        UniqueConstraint("livreur_id", "date_journee", name="uq_journees_trajets_livreur_date"),
        CheckConstraint("depart IN ('depot', 'domicile') AND retour IN ('depot', 'domicile')",
                        name="journee_points_valides"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    livreur_id: Mapped[int] = mapped_column(ForeignKey("utilisateurs.id", ondelete="CASCADE"), index=True)
    date_journee: Mapped[date] = mapped_column(Date, index=True)
    depart: Mapped[str] = mapped_column(String(10), default="depot", server_default="depot")
    retour: Mapped[str] = mapped_column(String(10), default="depot", server_default="depot")
    heure_depart: Mapped[time] = mapped_column(Time, default=time(8, 0))
    optimisee_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    distance_km: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    duree_min: Mapped[int | None] = mapped_column(Integer)
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EtapeTrajet(Base):
    __tablename__ = "etapes_trajets"
    __table_args__ = (
        CheckConstraint("duree_min BETWEEN 1 AND 480", name="etape_duree_plausible"),
        CheckConstraint("rdv_fin IS NULL OR rdv_debut IS NULL OR rdv_fin > rdv_debut", name="etape_rdv_coherent"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    journee_id: Mapped[int] = mapped_column(ForeignKey("journees_trajets.id", ondelete="CASCADE"), index=True)
    libelle: Mapped[str] = mapped_column(EncryptedText)          # « Contrôle technique »
    adresse: Mapped[str] = mapped_column(EncryptedText)          # adresse nettoyée
    position: Mapped[str] = mapped_column(EncryptedText)         # "lat;lon" (4 décimales)
    position_approximative: Mapped[bool] = mapped_column(default=False, server_default="false")
    rdv_debut: Mapped[time | None] = mapped_column(Time)         # rendez-vous facultatif
    rdv_fin: Mapped[time | None] = mapped_column(Time)
    duree_min: Mapped[int] = mapped_column(SmallInteger, default=15)
    ordre: Mapped[int | None] = mapped_column(SmallInteger)
    heure_prevue: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    faite_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
