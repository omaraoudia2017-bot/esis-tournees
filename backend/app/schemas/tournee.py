"""Schémas de l'écran « Ma tournée » (module 2)."""
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, Field


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
    nb_clients: int
    nb_optimises: int
    distance_avant_km: float
    distance_apres_km: float
    nb_retards: int
    duree_calcul_s: float
    a_reporter: list[str]


class DemandeFin(BaseModel):
    issue: Literal["livre", "absent", "refuse"] = Field(
        "livre", description="Résultat du passage (alimente l'historique des statuts)")
