"""Géocodage des clients sans coordonnées, puis rattachement aux zones.

Usage :
    docker compose exec api python -m scripts.geocoder_clients
    docker compose exec api python -m scripts.geocoder_clients --retenter-echecs

Environ 1 à 3 secondes par nouvelle adresse (limite Nominatim : 1 requête/s).
Interrompable à tout moment (Ctrl+C) : le travail déjà fait est enregistré et mis en cache.
"""
import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import or_, select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import Client
from app.models.enums import PrecisionGeocodage, SourceClient
from app.services.geocodage import Geocodeur, GeocoderServiceError, geocoder_avec_cache
from app.services.referentiel import appliquer_sectorisation

LOT = 10  # enregistrement tous les 10 clients


def main(retenter_echecs: bool) -> None:
    geocodeur = Geocodeur(get_settings().nominatim_user_agent)
    bilan, depuis_cache = Counter(), 0
    a_verifier: list[tuple[str, str, str]] = []

    with SessionLocal() as db:
        condition = Client.latitude.is_(None)
        if not retenter_echecs:
            condition = condition & or_(Client.geocode_precision.is_(None),
                                        Client.geocode_precision != PrecisionGeocodage.ECHEC)
        clients = db.scalars(
            select(Client).where(condition, Client.source != SourceClient.SIMULATION)
            .order_by(Client.id_client)
        ).all()
        print(f"{len(clients)} client(s) à géocoder")

        try:
            for i, client in enumerate(clients, start=1):
                res = geocoder_avec_cache(db, geocodeur, client.voie, client.code_postal, client.ville,
                                          retenter_echecs)
                client.latitude = None if res.latitude is None else Decimal(str(round(res.latitude, 6)))
                client.longitude = None if res.longitude is None else Decimal(str(round(res.longitude, 6)))
                client.geocode_precision = res.precision
                client.geocode_le = datetime.now(timezone.utc)
                bilan[res.precision.value] += 1
                depuis_cache += res.depuis_cache
                if res.precision in (PrecisionGeocodage.COMMUNE, PrecisionGeocodage.ECHEC):
                    a_verifier.append((client.id_client, client.adresse, res.precision.value))
                if i % LOT == 0:
                    db.commit()
                    print(f"  {i}/{len(clients)} - requêtes Nominatim : {geocodeur.nb_requetes}")
            db.commit()
        except GeocoderServiceError as e:
            db.commit()   # garde ce qui est déjà géocodé
            sys.exit(f"Service Nominatim indisponible ({e}). Relancez plus tard : "
                     f"les adresses déjà traitées ne seront pas redemandées.")
        except KeyboardInterrupt:
            db.commit()
            sys.exit("Interrompu : progression enregistrée.")

        zones = appliquer_sectorisation(db)
        db.commit()

    print("\nPrécision obtenue :", dict(bilan) or "rien à faire")
    print(f"Réponses depuis le cache : {depuis_cache} - requêtes envoyées : {geocodeur.nb_requetes}")
    print("Codes postaux par zone   :", dict(zones))
    if a_verifier:
        print("\nAdresses à vérifier (position approximative ou introuvable) :")
        for ident, adresse, precision in a_verifier:
            print(f"  - {ident} [{precision}] {adresse}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Géocodage Nominatim avec cache")
    parser.add_argument("--retenter-echecs", action="store_true",
                        help="redemander aussi les adresses marquées introuvables")
    main(parser.parse_args().retenter_echecs)
