"""Import de tous les modèles : nécessaire pour que Base.metadata les connaisse."""
from app.models.client import Client
from app.models.journal import JournalSecurite
from app.models.referentiel import Vehicule, Zone, ZoneCodePostal
from app.models.tournee import Livraison, Tournee
from app.models.utilisateur import Utilisateur

__all__ = [
    "Client", "JournalSecurite", "Livraison", "Tournee",
    "Utilisateur", "Vehicule", "Zone", "ZoneCodePostal",
]
