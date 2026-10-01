"""Import d'un fichier de tournées (format tournees_esis.csv) et géocodage des nouvelles adresses.

Utilisé par l'espace administrateur (module 4) et par scripts/importer_csv.py.
  - contrôle des colonnes et des champs obligatoires (nettoyage : services/nettoyage.py) ;
  - idempotent : un client déjà connu (même id_client) est mis à jour ; ses coordonnées ne sont
    remises à zéro que si l'adresse a changé ;
  - géocodage Nominatim limité à quelques adresses par appel (1 requête/s) : le reste est traité
    par lots, à la demande (« Géocoder les adresses en attente »).
"""
import csv
import io
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Client
from app.models.enums import PrecisionGeocodage, SourceClient, TypeCamion, TypeClient
from app.services.geocodage import GeocoderServiceError, geocoder_avec_cache
from app.services.nettoyage import (COLONNES_OBLIGATOIRES, ClientNettoye, LigneInvalide,
                                    nettoyer_ligne)
from app.services.referentiel import zone_id_pour

MAX_LIGNES = 5000


class ErreurImport(ValueError):
    """Fichier inexploitable dans son ensemble (colonnes manquantes, vide, trop gros)."""


@dataclass
class Rejet:
    ligne: int
    id_client: str
    motif: str


@dataclass
class BilanImport:
    lignes: int = 0
    crees: int = 0
    mis_a_jour: int = 0
    a_regeocoder: int = 0
    avec_avertissement: int = 0
    rejets: list[Rejet] = field(default_factory=list)
    ids_valides: list[str] = field(default_factory=list)


@dataclass
class BilanGeocodage:
    traites: int = 0
    precisions: dict[str, int] = field(default_factory=dict)
    restants: int = 0
    service_indisponible: bool = False


def lire_texte(texte: str) -> list[dict[str, str]]:
    """Lit le contenu d'un CSV (séparateur « , » ou « ; » des exports Excel français)."""
    texte = texte.lstrip("﻿")                 # BOM des exports Excel
    if not texte.strip():
        raise ErreurImport("Fichier vide.")
    try:
        dialecte = csv.Sniffer().sniff(texte[:4096], delimiters=",;")
    except csv.Error:
        dialecte = csv.excel
    lecteur = csv.DictReader(io.StringIO(texte, newline=""), dialect=dialecte)
    manquantes = set(COLONNES_OBLIGATOIRES) - {c.strip() for c in lecteur.fieldnames or []}
    if manquantes:
        raise ErreurImport("Colonnes obligatoires absentes : " + ", ".join(sorted(manquantes)))
    lignes = [{(k or "").strip(): v for k, v in ligne.items()} for ligne in lecteur]
    if len(lignes) > MAX_LIGNES:
        raise ErreurImport(f"Fichier trop volumineux : {MAX_LIGNES} lignes au maximum par import.")
    return lignes


def _appliquer(client: Client, c: ClientNettoye) -> None:
    client.nom_client = c.nom_client
    client.type_client = TypeClient.PROFESSIONNEL if c.professionnel else TypeClient.PARTICULIER
    client.adresse, client.voie = c.adresse, c.voie
    client.code_postal, client.ville = c.code_postal, c.ville
    client.telephone, client.digicode, client.notes_livraison = c.telephone, c.digicode, c.notes_livraison
    client.creneau_debut, client.creneau_fin = c.creneau_debut, c.creneau_fin
    client.est_immeuble, client.etage, client.ascenseur = c.est_immeuble, c.etage, c.ascenseur


def importer_lignes(db: Session, lignes: list[dict[str, str]]) -> BilanImport:
    """Crée ou met à jour les clients. L'appelant journalise et valide (commit)."""
    bilan = BilanImport(lignes=len(lignes))
    vus: set[str] = set()
    for num, ligne in enumerate(lignes, start=2):              # ligne 1 = en-tête
        try:
            c = nettoyer_ligne(ligne)
        except LigneInvalide as e:
            bilan.rejets.append(Rejet(num, (ligne.get("id_client") or "?").strip()[:10], str(e)))
            continue
        if c.id_client in vus:
            bilan.rejets.append(Rejet(num, c.id_client, "doublon dans le fichier"))
            continue
        vus.add(c.id_client)
        bilan.avec_avertissement += bool(c.avertissements)

        client = db.get(Client, c.id_client)
        if client is None:
            client = Client(id_client=c.id_client, source=SourceClient.IMPORT_CSV,
                            type_camion_requis=TypeCamion.NORMAL)
            _appliquer(client, c)
            db.add(client)
            bilan.crees += 1
        else:
            if client.source == SourceClient.SIMULATION:
                bilan.rejets.append(Rejet(num, c.id_client, "identifiant réservé au jeu simulé"))
                continue
            adresse_changee = client.adresse != c.adresse
            _appliquer(client, c)    # type de camion et position corrigés à la main : conservés
            client.actif = True
            if adresse_changee:
                client.latitude = client.longitude = client.geocode_precision = None
                client.geocode_le = client.zone_id = None
                bilan.a_regeocoder += 1
            bilan.mis_a_jour += 1
        bilan.ids_valides.append(c.id_client)
    db.flush()
    return bilan


def _en_attente():
    """Clients réels sans coordonnées, jamais déclarés introuvables."""
    return select(Client).where(
        Client.latitude.is_(None), Client.source != SourceClient.SIMULATION, Client.actif.is_(True),
        or_(Client.geocode_precision.is_(None), Client.geocode_precision != PrecisionGeocodage.ECHEC))


def nb_en_attente(db: Session) -> int:
    return len(db.scalars(_en_attente().with_only_columns(Client.id_client)).all())


def geocoder_en_attente(db: Session, geocodeur, maximum: int,
                        ids: list[str] | None = None) -> BilanGeocodage:
    """Géocode au plus `maximum` clients en attente (cache d'abord, Nominatim sinon) et les
    rattache à leur zone. Les adresses en cache ne comptent pas dans le maximum."""
    bilan, precisions = BilanGeocodage(), Counter()
    requete = _en_attente().order_by(Client.id_client)
    if ids:
        requete = requete.where(Client.id_client.in_(ids))
    nouvelles = 0
    try:
        for client in db.scalars(requete).all():
            if nouvelles >= maximum:
                break
            res = geocoder_avec_cache(db, geocodeur, client.voie or client.adresse,
                                      client.code_postal, client.ville)
            nouvelles += not res.depuis_cache
            client.geocode_precision, client.geocode_le = res.precision, datetime.now(timezone.utc)
            if res.latitude is not None:
                client.latitude = Decimal(str(round(res.latitude, 6)))
                client.longitude = Decimal(str(round(res.longitude, 6)))
                client.zone_id = zone_id_pour(db, client.code_postal, res.latitude, res.longitude)
            precisions[res.precision.value] += 1
            bilan.traites += 1
            db.flush()
    except GeocoderServiceError:
        bilan.service_indisponible = True        # déjà géocodés : conservés ; le reste attendra
    bilan.precisions = dict(precisions)
    bilan.restants = nb_en_attente(db)
    return bilan
