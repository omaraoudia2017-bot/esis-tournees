"""Création des zones et rattachement des clients à leur zone."""
from collections import Counter

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Client, Zone, ZoneCodePostal
from app.models.enums import PrecisionGeocodage, SourceClient
from app.services.sectorisation import ZONES, zone_pour


def assurer_zones(db: Session) -> dict[str, Zone]:
    """Crée les 3 zones si elles n'existent pas encore (idempotent)."""
    existantes = {z.nom: z for z in db.scalars(select(Zone))}
    for definition in ZONES:
        if definition.nom not in existantes:
            zone = Zone(nom=definition.nom, description=definition.description,
                        max_clients=definition.max_clients)
            db.add(zone)
            existantes[definition.nom] = zone
    db.flush()
    return existantes


def appliquer_sectorisation(db: Session) -> Counter:
    """Rattache chaque code postal (clients réels géocodés) à une zone, puis chaque client."""
    zones = assurer_zones(db)
    centroides = db.execute(
        select(Client.code_postal, func.avg(Client.latitude), func.avg(Client.longitude))
        .where(Client.latitude.is_not(None), Client.source != SourceClient.SIMULATION,
               Client.geocode_precision != PrecisionGeocodage.ECHEC)
        .group_by(Client.code_postal)
    ).all()

    bilan = Counter()
    for cp, lat, lon in centroides:
        definition = zone_pour(cp, float(lat), float(lon))
        db.merge(ZoneCodePostal(code_postal=cp, zone_id=zones[definition.nom].id))
        bilan[definition.nom] += 1
    db.flush()

    db.execute(
        update(Client).values(
            zone_id=select(ZoneCodePostal.zone_id)
            .where(ZoneCodePostal.code_postal == Client.code_postal)
            .scalar_subquery()
        ).execution_options(synchronize_session=False)
    )
    return bilan


def zone_id_pour(db: Session, code_postal: str, latitude: float, longitude: float) -> int | None:
    """Zone d'un client : rattachement connu du code postal, sinon règle de distance."""
    rattachement = db.get(ZoneCodePostal, code_postal)
    if rattachement is not None:
        return rattachement.zone_id
    return db.scalar(select(Zone.id).where(Zone.nom == zone_pour(code_postal, latitude, longitude).nom))
