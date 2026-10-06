"""Tournées quotidiennes et livraisons (arrêts), avec pointage terrain."""
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (Boolean, CheckConstraint, Computed, Date, DateTime, ForeignKey,
                        Integer, Numeric, SmallInteger, Time, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import StatutLivraison, StatutTournee, pg_enum


class Tournee(Base):
    __tablename__ = "tournees"
    __table_args__ = (
        UniqueConstraint("livreur_id", "date_tournee", name="uq_tournees_livreur_date"),
        CheckConstraint("max_clients BETWEEN 10 AND 20", name="max_clients_10_20"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    date_tournee: Mapped[date] = mapped_column(Date, index=True)
    # NULL = tournée « en attente » : attribution annulée par le planificateur, à redonner
    livreur_id: Mapped[int | None] = mapped_column(ForeignKey("utilisateurs.id", ondelete="RESTRICT"))
    vehicule_id: Mapped[int | None] = mapped_column(ForeignKey("vehicules.id", ondelete="SET NULL"))
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id", ondelete="SET NULL"))
    statut: Mapped[StatutTournee] = mapped_column(
        pg_enum(StatutTournee, "statut_tournee"), default=StatutTournee.PLANIFIEE
    )
    # Limite d'effectif figée à la création (copiée depuis la zone). Vérifiée par trigger.
    max_clients: Mapped[int] = mapped_column(SmallInteger, default=15)
    # Période de la tournée (matin / après-midi / soirée) : départ du dépôt et retour au plus tard.
    # NULL = journée complète (HEURE_DEBUT_TOURNEE -> HEURE_FIN_TOURNEE du .env)
    heure_depart: Mapped[time | None] = mapped_column(Time)
    heure_fin: Mapped[time | None] = mapped_column(Time)

    # Résultats de la dernière optimisation OR-Tools
    optimisee_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    distance_totale_km: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    duree_estimee_min: Mapped[int | None] = mapped_column(Integer)
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Livraison(Base):
    """Un arrêt = un client dans une tournée. Remplace la colonne historique_statut du CSV."""
    __tablename__ = "livraisons"
    __table_args__ = (
        UniqueConstraint("tournee_id", "client_id", name="uq_livraisons_tournee_client"),
        CheckConstraint(
            "heure_fin_livraison IS NULL OR heure_debut_livraison IS NOT NULL",
            name="fin_apres_debut_saisi",
        ),
        CheckConstraint(
            "heure_fin_livraison IS NULL OR heure_fin_livraison >= heure_debut_livraison",
            name="fin_posterieure_debut",
        ),
        CheckConstraint("ordre_passage >= 1", name="ordre_positif"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tournee_id: Mapped[int] = mapped_column(
        ForeignKey("tournees.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id_client", ondelete="RESTRICT"), index=True
    )
    ordre_passage: Mapped[int | None] = mapped_column(SmallInteger)
    statut: Mapped[StatutLivraison] = mapped_column(
        pg_enum(StatutLivraison, "statut_livraison"), default=StatutLivraison.A_LIVRER
    )
    ajout_a_la_volee: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    heure_arrivee_prevue: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Pointage terrain (boutons Démarrer / Terminer)
    heure_debut_livraison: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heure_fin_livraison: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Calculé par PostgreSQL : aucune incohérence possible avec les horodatages
    temps_service_reel_s: Mapped[int | None] = mapped_column(
        Integer,
        Computed(
            "CAST(EXTRACT(EPOCH FROM (heure_fin_livraison - heure_debut_livraison)) AS INTEGER)",
            persisted=True,
        ),
    )
    # Estimation utilisée par OR-Tools (valeur théorique puis prédiction ML)
    temps_service_predit_s: Mapped[int | None] = mapped_column(Integer)
