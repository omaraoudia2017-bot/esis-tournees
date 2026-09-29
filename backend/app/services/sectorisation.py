"""Règles de sectorisation : chaque code postal est rattaché à une zone,
qui fixe la limite d'effectif d'une tournée (10 à 20 clients)."""
from dataclasses import dataclass

from app.services.distances import LYON_CENTRE, haversine_km


@dataclass(frozen=True)
class DefinitionZone:
    nom: str
    description: str
    max_clients: int


URBAIN = DefinitionZone("Lyon - Villeurbanne", "Zone urbaine dense, arrêts rapprochés", 20)
COURONNE = DefinitionZone("Première couronne", "Communes à moins de 12 km du centre de Lyon", 15)
PERIURBAIN = DefinitionZone("Périurbain et rural", "Au-delà de 12 km, trajets plus longs", 10)
ZONES = [URBAIN, COURONNE, PERIURBAIN]

RAYON_COURONNE_KM = 12.0
CODES_POSTAUX_URBAINS = {f"6900{i}" for i in range(1, 10)} | {"69100"}


def zone_pour(code_postal: str, latitude: float, longitude: float) -> DefinitionZone:
    if code_postal in CODES_POSTAUX_URBAINS:
        return URBAIN
    if haversine_km(latitude, longitude, *LYON_CENTRE) <= RAYON_COURONNE_KM:
        return COURONNE
    return PERIURBAIN
