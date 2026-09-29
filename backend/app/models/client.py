"""Fiches clients, construites à partir du format de l'échantillon tournees_esis.csv.

Correspondance CSV -> table :
  id_client               -> id_client (10 chiffres, zéros initiaux conservés => texte)
  nom_client              -> nom_client
  adresse                 -> adresse nettoyée + voie / code_postal / ville (découpées) ;
                             les compléments (étage, bâtiment, code...) partent dans les notes chiffrées
  telephone               -> telephone        (chiffré)
  digicode                -> digicode         (chiffré, peut contenir plusieurs codes "0629 / 3263")
  creneau_horaire_prefere -> creneau_debut / creneau_fin ("08H00-10H00")
  historique_statut       -> table livraisons (un statut par passage)
  notes_livraison         -> notes_livraison  (chiffré : contient téléphones et infos personnelles)
                             + etage / ascenseur / est_immeuble extraits par Regex (features ML, en clair)
"""
from datetime import datetime, time
from decimal import Decimal

from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, Numeric,
                        SmallInteger, String, Time, func)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import EncryptedText
from app.models.enums import (PRECISION_GEOCODAGE_PG, TYPE_CAMION_PG, PrecisionGeocodage,
                              SourceClient, TypeCamion, TypeClient, pg_enum)


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (
        CheckConstraint("id_client ~ '^[0-9]{10}$'", name="id_client_format"),
        CheckConstraint("code_postal ~ '^[0-9]{5}$'", name="code_postal_format"),
        CheckConstraint("creneau_fin > creneau_debut", name="creneau_coherent"),
        CheckConstraint("etage BETWEEN -5 AND 60", name="etage_plausible"),
        CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)",
            name="coordonnees_valides",
        ),
    )

    id_client: Mapped[str] = mapped_column(String(10), primary_key=True)
    nom_client: Mapped[str] = mapped_column(String(100))
    type_client: Mapped[TypeClient] = mapped_column(
        pg_enum(TypeClient, "type_client"), default=TypeClient.PARTICULIER
    )

    # Adresse
    adresse: Mapped[str] = mapped_column(String(255))
    voie: Mapped[str | None] = mapped_column(String(200))
    code_postal: Mapped[str] = mapped_column(String(5), index=True)
    ville: Mapped[str] = mapped_column(String(100))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    geocode_precision: Mapped[PrecisionGeocodage | None] = mapped_column(
        PRECISION_GEOCODAGE_PG
    )
    geocode_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Données sensibles (chiffrées Fernet)
    telephone: Mapped[str | None] = mapped_column(EncryptedText)
    digicode: Mapped[str | None] = mapped_column(EncryptedText)
    notes_livraison: Mapped[str | None] = mapped_column(EncryptedText)

    # Fenêtre horaire (contrainte CVRPTW)
    creneau_debut: Mapped[time | None] = mapped_column(Time)
    creneau_fin: Mapped[time | None] = mapped_column(Time)

    # Segmentation de flotte + caractéristiques d'accès (variables du modèle ML)
    type_camion_requis: Mapped[TypeCamion] = mapped_column(
        TYPE_CAMION_PG, default=TypeCamion.NORMAL
    )
    est_immeuble: Mapped[bool | None] = mapped_column(Boolean)
    etage: Mapped[int | None] = mapped_column(SmallInteger)
    ascenseur: Mapped[bool | None] = mapped_column(Boolean)

    zone_id: Mapped[int | None] = mapped_column(
        ForeignKey("zones.id", ondelete="SET NULL"), index=True
    )
    source: Mapped[SourceClient] = mapped_column(
        pg_enum(SourceClient, "source_client"), default=SourceClient.IMPORT_CSV
    )
    actif: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    # RGPD : sert à calculer la durée de conservation (purge des clients inactifs)
    derniere_livraison_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    modifie_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
