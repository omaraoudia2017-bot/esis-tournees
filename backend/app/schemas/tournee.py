"""Schémas de l'écran « Ma tournée » (module 2)."""
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Point(BaseModel):
    nom: str
    latitude: float
    longitude: float


class ClientVue(BaseModel):
    id_client: str
    nom: str
    adresse: str
    code_postal: str
    ville: str
    latitude: float | None
    longitude: float | None
    creneau_debut: time | None
    creneau_fin: time | None
    type_camion_requis: str
    etage: int | None
    ascenseur: bool | None
    # Données chiffrées en base, déchiffrées uniquement pour le livreur affecté à la tournée
    telephone: str | None
    digicode: str | None
    notes_livraison: str | None


class LivraisonVue(BaseModel):
    id: int
    ordre_passage: int | None
    statut: str
    ajout_a_la_volee: bool
    heure_arrivee_prevue: datetime | None
    heure_debut_livraison: datetime | None
    heure_fin_livraison: datetime | None
    temps_service_reel_s: int | None
    temps_service_predit_s: int | None
    client: ClientVue


class TourneeVue(BaseModel):
    id: int
    date_tournee: date
    statut: str
    zone: str | None
    vehicule: str | None
    type_camion: str | None
    max_clients: int
    heure_depart: time
    heure_fin: time
    optimisee_le: datetime | None
    distance_totale_km: float | None
    duree_estimee_min: int | None
    depot: Point
    livraisons: list[LivraisonVue]


class ReponseOptimisation(BaseModel):
    mode: Literal["complet", "restant"]
    nb_clients: int
    nb_optimises: int
    distance_avant_km: float
    distance_apres_km: float
    nb_retards: int
    duree_calcul_s: float
    a_reporter: list[str]
    temps_service: Literal["modele", "fixe"] = "fixe"


class DemandeFin(BaseModel):
    issue: Literal["livre", "absent", "refuse"] = Field(
        "livre", description="Résultat du passage (alimente l'historique des statuts)")


class DemandeAjoutClient(BaseModel):
    """Nouvelle adresse saisie par le livreur en cours de tournée (module 3).
    Aucun identifiant de client existant n'est accepté : un livreur ne doit pas pouvoir
    rattacher n'importe quel client de la base à sa tournée pour en lire le digicode."""
    nom: str = Field(min_length=2, max_length=100)
    voie: str = Field(min_length=3, max_length=200, description="numéro et rue")
    code_postal: str = Field(pattern=r"^[0-9]{5}$")
    ville: str = Field(min_length=2, max_length=100)
    type_camion_requis: Literal["normal", "hayon"] = "normal"
    creneau_debut: time | None = None
    creneau_fin: time | None = None
    telephone: str | None = Field(None, max_length=20, pattern=r"^[0-9 +.]{6,20}$")
    digicode: str | None = Field(None, max_length=50)
    notes_livraison: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def creneau_coherent(self):
        if (self.creneau_debut is None) != (self.creneau_fin is None):
            raise ValueError("indiquez le début ET la fin du créneau, ou aucun des deux")
        if self.creneau_debut is not None and self.creneau_fin <= self.creneau_debut:
            raise ValueError("la fin du créneau doit être après le début")
        return self


class ReponseAjout(BaseModel):
    livraison: LivraisonVue
    avertissement: str | None = None
