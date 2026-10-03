"""Schémas Pydantic : validation des entrées et forme des réponses de l'API."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DemandeConnexion(BaseModel):
    identifiant: str = Field(min_length=1, max_length=50)
    mot_de_passe: str = Field(min_length=1, max_length=128)


class ReponseEtape1(BaseModel):
    # nouveau_mot_de_passe : connexion avec le code d'activation, le mot de passe est à choisir
    etape: Literal["verification", "enrolement", "nouveau_mot_de_passe"]
    jeton_preauth: str
    expire_dans_s: int


class DemandeNouveauMdp(BaseModel):
    nouveau: str = Field(min_length=1, max_length=128)
    confirmation: str = Field(min_length=1, max_length=128)


class DemandeCode(BaseModel):
    code: str = Field(min_length=6, max_length=20, description="Code TOTP à 6 chiffres ou code de secours")


class ReponseEnrolement(BaseModel):
    uri_otpauth: str
    qr_code_png_base64: str
    cle_manuelle: str = Field(description="À saisir si le QR code ne peut pas être scanné")


class ReponseJeton(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expire_le: datetime
    role: str


class ReponseActivation(ReponseJeton):
    codes_secours: list[str] = Field(description="Affichés une seule fois : à conserver hors du téléphone")


class ProfilUtilisateur(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    identifiant: str
    nom: str
    prenom: str
    role: str
    totp_active: bool
    derniere_connexion: datetime | None
    # Livreur : tant que la version en vigueur des conditions n'est pas acceptée, seules les routes
    # /auth et /conditions répondent (les autres renvoient 403)
    type_livreur: str | None = None            # "interne" / "prestataire" (livreur uniquement)
    domicile_renseigne: bool = False
    conditions_a_accepter: bool = False
    conditions_version: str | None = None
    conditions_acceptees_le: datetime | None = None


class EvenementJournal(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    horodatage: datetime
    evenement: str
    succes: bool
    utilisateur_id: int | None
    identifiant_saisi: str | None
    adresse_ip: str | None
    details: dict | None


class ConditionsVue(BaseModel):
    version: str                       # version en vigueur
    texte: str                         # Markdown
    a_accepter: bool
    version_acceptee: str | None
    acceptee_le: datetime | None


class DemandeAcceptation(BaseModel):
    """Deux cases distinctes : accepter les règles d'usage, et attester avoir lu l'information
    RGPD (on ne « consent » pas à un traitement fondé sur le contrat ou l'intérêt légitime)."""
    version: str = Field(max_length=10)
    conditions_acceptees: Literal[True]
    information_lue: Literal[True]
