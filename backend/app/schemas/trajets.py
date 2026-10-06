"""Schémas de la « journée de trajets » : jours sans tournée, le livreur saisit ses étapes
(garage, contrôle technique, récupération d'une pièce…) et les fait optimiser."""
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.tournee import Point


class DemandeEtape(BaseModel):
    libelle: str = Field(min_length=2, max_length=80, description="par ex. « Contrôle technique »")
    voie: str = Field(min_length=3, max_length=200, description="numéro et rue")
    code_postal: str = Field(pattern=r"^[0-9]{5}$")
    ville: str = Field(min_length=2, max_length=100)
    rdv_debut: time | None = None
    rdv_fin: time | None = None
    duree_min: int | None = Field(None, ge=1, le=480, description="temps sur place (15 min par défaut)")

    @model_validator(mode="after")
    def rdv_coherent(self):
        if (self.rdv_debut is None) != (self.rdv_fin is None):
            raise ValueError("indiquez le début ET la fin du rendez-vous, ou aucun des deux")
        if self.rdv_debut is not None and self.rdv_fin <= self.rdv_debut:
            raise ValueError("la fin du rendez-vous doit être après le début")
        return self


class DemandeReglages(BaseModel):
    depart: Literal["depot", "domicile"] = "depot"
    retour: Literal["depot", "domicile"] = "depot"
    heure_depart: time = time(8, 0)


class EtapeVue(BaseModel):
    id: int
    ordre: int | None
    libelle: str
    adresse: str
    latitude: float
    longitude: float
    position_approximative: bool
    rdv_debut: time | None
    rdv_fin: time | None
    duree_min: int
    heure_prevue: datetime | None
    faite_le: datetime | None


class JourneeVue(BaseModel):
    date_journee: date
    tournee_attribuee: bool                  # information : une tournée est aussi prévue ce jour-là
    depart: str = "depot"
    retour: str = "depot"
    heure_depart: time = time(8, 0)
    domicile_disponible: bool = False        # livreur interne ayant renseigné son adresse
    optimisee_le: datetime | None = None
    distance_km: float | None = None
    duree_min: int | None = None
    point_depart: Point
    point_arrivee: Point
    max_etapes: int
    duree_defaut_min: int
    etapes: list[EtapeVue] = []


class ReponseEtape(BaseModel):
    etape: EtapeVue
    avertissement: str | None = None


class ReponseTrajets(BaseModel):
    mode: Literal["complet", "restant"]
    nb_etapes: int
    distance_avant_km: float
    distance_apres_km: float
    nb_retards: int
    duree_calcul_s: float


# ------------------------------------------------------------------ vue du planificateur (lecture seule)
class EtapePlanificateur(BaseModel):
    ordre: int | None
    libelle: str
    adresse: str
    rdv_debut: time | None
    rdv_fin: time | None
    duree_min: int
    heure_prevue: datetime | None
    faite_le: datetime | None


class JourneePlanificateur(BaseModel):
    """Aucune position ni adresse de domicile : seulement « Dépôt » ou « Domicile »."""
    livreur: str
    livreur_nom: str
    depart: str
    retour: str
    heure_depart: time
    optimisee: bool
    distance_km: float | None
    duree_min: int | None
    nb_faites: int
    etapes: list[EtapePlanificateur]
