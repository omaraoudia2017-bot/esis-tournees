"""Schémas de l'espace administrateur (module 4)."""
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, Field

IDENTIFIANT = r"^[a-z0-9][a-z0-9._-]{2,49}$"
ID_CLIENT = r"^[0-9]{10}$"


# --------------------------------------------------------------------------- comptes
class CompteVue(BaseModel):
    id: int
    identifiant: str
    nom: str
    prenom: str
    role: str
    actif: bool
    totp_active: bool
    verrouille: bool
    vehicule_id: int | None
    vehicule: str | None
    type_camion: str | None
    derniere_connexion: datetime | None
    conditions_version: str | None = None
    conditions_acceptees_le: datetime | None = None
    type_livreur: str | None = None
    domicile_renseigne: bool = False           # jamais l'adresse elle-même (minimisation)
    activation_en_attente: bool = False        # code d'activation remis, mot de passe pas encore choisi
    activation_expire_le: datetime | None = None


class DemandeCompte(BaseModel):
    identifiant: str = Field(pattern=IDENTIFIANT, description="minuscules, chiffres, . _ -")
    nom: str = Field(min_length=1, max_length=80)
    prenom: str = Field(min_length=1, max_length=80)
    role: Literal["livreur", "admin"] = "livreur"
    vehicule_id: int | None = None
    # Livreur : interne (rentre avec le camion, départ/retour à son domicile) ou prestataire
    # (rend le camion au dépôt). Par défaut : prestataire.
    type_livreur: Literal["interne", "prestataire"] | None = None


class ReponseCompte(BaseModel):
    compte: CompteVue
    code_activation: str | None = Field(
        None, description="Affiché une seule fois : à remettre au salarié par un canal sûr. Il sert "
                          "à la première connexion, pour choisir son mot de passe.")
    code_expire_le: datetime | None = None


class DemandeTypeLivreur(BaseModel):
    type_livreur: Literal["interne", "prestataire"]


class DemandeVehicule(BaseModel):
    vehicule_id: int | None = None


class VehiculeVue(BaseModel):
    id: int
    immatriculation: str
    type_camion: str
    actif: bool
    utilise_par: str | None


# --------------------------------------------------------------------------- import CSV
class DemandeImport(BaseModel):
    nom_fichier: str = Field(min_length=1, max_length=100)
    contenu: str = Field(min_length=1, max_length=2_000_000, description="texte du fichier CSV")
    max_geocodages: int = Field(15, ge=0, le=60, description="nouvelles adresses géocodées tout de suite")


class RejetVue(BaseModel):
    ligne: int
    id_client: str
    motif: str


class BilanGeocodageVue(BaseModel):
    traites: int
    precisions: dict[str, int]
    restants: int
    service_indisponible: bool


class ReponseImport(BaseModel):
    lignes: int
    crees: int
    mis_a_jour: int
    a_regeocoder: int
    avec_avertissement: int
    rejets: list[RejetVue]
    ids_valides: list[str]
    geocodage: BilanGeocodageVue


class DemandeGeocodage(BaseModel):
    maximum: int = Field(20, ge=1, le=60)


# --------------------------------------------------------------------------- tournées
class TourneeResume(BaseModel):
    id: int
    date_tournee: date
    livreur: str
    livreur_nom: str
    vehicule: str | None
    type_camion: str | None
    zone: str | None
    statut: str
    nb_clients: int
    max_clients: int
    nb_terminees: int
    nb_a_reporter: int
    heure_depart: time | None
    heure_fin: time | None
    optimisee: bool
    distance_totale_km: float | None


class DemandeTournee(BaseModel):
    livreur_id: int
    clients: list[str] = Field(min_length=1, max_length=20)
    date_tournee: date | None = None
    periode: Literal["matin", "apres-midi", "soiree", "journee"] = "matin"
    remplacer: bool = False


class DemandeAjoutExistant(BaseModel):
    id_client: str = Field(pattern=ID_CLIENT)


# --------------------------------------------------------------------------- clients
class ClientResume(BaseModel):
    """Pas de téléphone, digicode ni notes dans les listes : uniquement dans l'export RGPD."""
    id_client: str
    nom: str
    adresse: str
    code_postal: str
    ville: str
    zone: str | None
    latitude: float | None
    longitude: float | None
    precision: str | None
    type_camion_requis: str
    creneau_debut: time | None
    creneau_fin: time | None
    source: str
    actif: bool


class DemandeCorrection(BaseModel):
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    type_camion_requis: Literal["normal", "hayon"] | None = None


class DemandeAnonymisation(BaseModel):
    confirmation: str = Field(pattern=ID_CLIENT, description="ressaisir l'identifiant du client")


# --------------------------------------------------------------------------- tableau de bord
class TableauDeBordVue(BaseModel):
    debut: date
    fin: date
    nb_tournees: int
    nb_livraisons: int
    statuts: dict[str, int]
    creneaux_controles: int
    taux_respect_creneaux: float | None
    taux_remplissage: float | None
    pointages_retenus: int
    ecart_moyen_predit_min: float | None
    biais_predit_min: float | None
    ecart_moyen_fixe_min: float | None
    alertes_connexion: int
    echecs_connexion: int
    comptes_verrouilles: int
    par_jour: list[dict]
    par_tournee: list[dict]
