"""Schémas Pydantic : validation des entrées et forme des réponses de l'API."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DemandeConnexion(BaseModel):
    identifiant: str = Field(min_length=1, max_length=50)
    mot_de_passe: str = Field(min_length=1, max_length=128)


class ReponseEtape1(BaseModel):
    etape: Literal["verification", "enrolement"]
    jeton_preauth: str
    expire_dans_s: int


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


class EvenementJournal(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    horodatage: datetime
    evenement: str
    succes: bool
    utilisateur_id: int | None
    identifiant_saisi: str | None
    adresse_ip: str | None
    details: dict | None
