"""Géocodage des adresses avec Nominatim (OpenStreetMap), via geopy, avec cache PostgreSQL.

Politique d'usage Nominatim respectée : 1 requête par seconde maximum, user-agent identifiant
l'application, et aucune adresse demandée deux fois (cache, y compris pour les échecs).
"""
from dataclasses import dataclass
from decimal import Decimal

from geopy.exc import GeocoderServiceError
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim
from sqlalchemy.orm import Session

from app.models import CacheGeocodage
from app.models.enums import PrecisionGeocodage

__all__ = ["Geocodeur", "GeocoderServiceError", "ResultatGeocodage", "geocoder_avec_cache"]

CLES_RUE = ("road", "pedestrian", "footway", "residential", "path", "square", "place")


@dataclass
class ResultatGeocodage:
    latitude: float | None
    longitude: float | None
    precision: PrecisionGeocodage
    libelle: str | None = None
    depuis_cache: bool = False


class Geocodeur:
    """Enveloppe Nominatim : 3 stratégies successives, de la plus précise à la plus large."""

    def __init__(self, user_agent: str, delai_s: float = 1.1):
        nominatim = Nominatim(user_agent=user_agent, timeout=10)
        self._geocode = RateLimiter(
            nominatim.geocode,
            min_delay_seconds=delai_s,
            max_retries=2,
            error_wait_seconds=5.0,
            swallow_exceptions=False,  # une panne réseau ne doit pas être enregistrée comme "introuvable"
        )
        self.nb_requetes = 0

    def _appel(self, requete):
        self.nb_requetes += 1
        return self._geocode(requete, country_codes="fr", addressdetails=True, exactly_one=True)

    @staticmethod
    def _precision(lieu, code_postal: str) -> PrecisionGeocodage | None:
        """ADRESSE / RUE si le résultat est fiable, None sinon."""
        if lieu is None:
            return None
        adresse = lieu.raw.get("address", {})
        cp_osm = adresse.get("postcode", "")
        if cp_osm and cp_osm[:2] != code_postal[:2]:      # autre département : résultat rejeté
            return None
        if "house_number" in adresse:
            return PrecisionGeocodage.ADRESSE
        if any(cle in adresse for cle in CLES_RUE):
            return PrecisionGeocodage.RUE
        return None                                       # simple commune : pas assez précis ici

    def geocoder(self, voie: str, code_postal: str, ville: str) -> ResultatGeocodage:
        strategies = [
            {"street": voie, "postalcode": code_postal, "city": ville, "country": "France"},
            f"{voie}, {code_postal} {ville}, France",
        ]
        for requete in strategies:
            lieu = self._appel(requete)
            precision = self._precision(lieu, code_postal)
            if precision:
                return ResultatGeocodage(lieu.latitude, lieu.longitude, precision, lieu.address)

        # Dernier recours : centre de la commune (à faire corriger par l'administrateur)
        lieu = self._appel({"postalcode": code_postal, "city": ville, "country": "France"})
        if lieu is not None:
            return ResultatGeocodage(lieu.latitude, lieu.longitude, PrecisionGeocodage.COMMUNE,
                                     lieu.address)
        return ResultatGeocodage(None, None, PrecisionGeocodage.ECHEC)


def cle_cache(voie: str, code_postal: str, ville: str) -> str:
    return f"{voie}|{code_postal}|{ville}".upper()[:320]


def geocoder_avec_cache(db: Session, geocodeur: Geocodeur, voie: str, code_postal: str,
                        ville: str, retenter_echecs: bool = False) -> ResultatGeocodage:
    """Consulte le cache ; n'interroge Nominatim que pour une adresse jamais vue.

    Lève GeocoderServiceError si le service est injoignable (rien n'est mis en cache).
    """
    cle = cle_cache(voie, code_postal, ville)
    en_cache = db.get(CacheGeocodage, cle)
    if en_cache is not None and retenter_echecs and en_cache.precision == PrecisionGeocodage.ECHEC:
        db.delete(en_cache)
        db.flush()
        en_cache = None
    if en_cache is not None:
        return ResultatGeocodage(
            float(en_cache.latitude) if en_cache.latitude is not None else None,
            float(en_cache.longitude) if en_cache.longitude is not None else None,
            en_cache.precision, en_cache.libelle_osm, depuis_cache=True,
        )

    res = geocodeur.geocoder(voie, code_postal, ville)
    db.add(CacheGeocodage(
        cle=cle,
        latitude=_arrondi(res.latitude),
        longitude=_arrondi(res.longitude),
        precision=res.precision,
        libelle_osm=(res.libelle or "")[:500] or None,
    ))
    return res


def _arrondi(valeur: float | None) -> Decimal | None:
    return None if valeur is None else Decimal(str(round(valeur, 6)))
