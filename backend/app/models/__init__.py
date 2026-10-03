"""Import de tous les modèles : nécessaire pour que Base.metadata les connaisse."""
from app.models.client import Client
from app.models.geocodage import CacheGeocodage
from app.models.journal import JournalSecurite
from app.models.referentiel import Vehicule, Zone, ZoneCodePostal
from app.models.remarque import RemarqueClient
from app.models.tournee import Livraison, Tournee
from app.models.utilisateur import CodeSecours, JetonRevoque, Utilisateur

__all__ = [
    "CacheGeocodage", "Client", "CodeSecours", "JetonRevoque", "JournalSecurite", "Livraison",
    "RemarqueClient", "Tournee", "Utilisateur", "Vehicule", "Zone", "ZoneCodePostal",
]
