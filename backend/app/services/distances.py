"""Distances géographiques (réutilisées par la sectorisation et par le solveur OR-Tools)."""
from math import asin, cos, radians, sin, sqrt

RAYON_TERRE_KM = 6371.0
LYON_CENTRE = (45.7578, 4.8320)  # place Bellecour


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance à vol d'oiseau entre deux points GPS, en kilomètres."""
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = p2 - p1, radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * RAYON_TERRE_KM * asin(sqrt(a))
