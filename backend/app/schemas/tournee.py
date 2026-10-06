"""Schémas de l'écran « Ma tournée » (module 2)."""
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Point(BaseModel):
    nom: str
    latitude: float
    longitude: float


class RemarqueVue(BaseModel):
    id: int
    texte: str
    cree_le: datetime
    de_moi: bool = False          # le livreur connecté peut supprimer ses propres remarques


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
    # Remarques laissées sur cette adresse lors des passages précédents (les plus récentes)
    remarques: list[RemarqueVue] = []


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


class AnnulationPossible(BaseModel):
    """Dernier clic Démarrer / Terminer encore annulable (sinon le champ vaut null)."""
    action: Literal["demarrer", "terminer"]
    livraison_id: int
    client: str
    expire_le: datetime


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
    # Livreur interne : départ domicile -> dépôt, retour au domicile sauf colis non livrés
    type_livreur: str | None = None
    retour: Literal["depot", "domicile"] = "depot"
    colis_a_rapporter: int = 0                 # absents, refus, clients à reporter
    domicile: Point | None = None              # visible par le livreur lui-même uniquement
    trajet_domicile_depot_min: int | None = None
    annulation_possible: AnnulationPossible | None = None
    # Tournée d'un autre jour consultée par le livreur : lecture seule, sans digicode, téléphone ni
    # notes (ils ne sont utiles que le jour même : minimisation des données)
    consultation_seule: bool = False


class TourneeLivreurResume(BaseModel):
    """Une ligne du calendrier du livreur (tournées passées et à venir)."""
    id: int
    date_tournee: date
    statut: str
    zone: str | None
    heure_depart: time | None
    heure_fin: time | None
    nb_clients: int
    nb_livres: int
    nb_non_livres: int                 # absents, refus, à reporter
    distance_totale_km: float | None
    optimisee: bool


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
    retour: Literal["depot", "domicile"] = "depot"


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


class ReponseAnnulation(BaseModel):
    action_annulee: Literal["demarrer", "terminer"]
    message: str
    livraison: LivraisonVue


class DemandeRemarque(BaseModel):
    texte: str = Field(min_length=3, max_length=300,
                       description="Information utile pour les prochaines livraisons à cette adresse")


class DemandeDomicile(BaseModel):
    """Adresse de départ et de retour du livreur interne (saisie par le livreur lui-même)."""
    voie: str = Field(min_length=3, max_length=200, description="numéro et rue")
    code_postal: str = Field(pattern=r"^[0-9]{5}$")
    ville: str = Field(min_length=2, max_length=100)


class DomicileVue(BaseModel):
    type_livreur: str | None
    renseigne: bool
    adresse: str | None                        # renvoyée au livreur lui-même uniquement
    modifie_le: datetime | None
    trajet_depot_km: float | None = None
    trajet_depot_min: int | None = None
    avertissement: str | None = None
