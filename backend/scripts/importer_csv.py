"""Import et nettoyage d'un fichier de tournées au format tournees_esis.csv.

Usage :
    docker compose exec api python -m scripts.importer_csv                       # fichier par défaut
    docker compose exec api python -m scripts.importer_csv /data/raw/autre.csv

Idempotent : relancer l'import met à jour les clients existants (clé = id_client).
Les coordonnées GPS ne sont réinitialisées que si l'adresse a changé.
"""
import argparse
import csv
import sys
from pathlib import Path

from app.db.session import SessionLocal
from app.models import Client, JournalSecurite
from app.models.enums import SourceClient, TypeCamion, TypeClient
from app.services.nettoyage import (COLONNES_OBLIGATOIRES, ClientNettoye, LigneInvalide,
                                    nettoyer_ligne)

FICHIER_DEFAUT = "/data/raw/tournees_esis.csv"


def lire_csv(chemin: Path) -> list[dict[str, str]]:
    with chemin.open(encoding="utf-8-sig", newline="") as f:   # utf-8-sig : BOM des exports Excel
        echantillon = f.read(4096)
        f.seek(0)
        try:
            dialecte = csv.Sniffer().sniff(echantillon, delimiters=",;")   # Excel FR exporte en ";"
        except csv.Error:
            dialecte = csv.excel
        lecteur = csv.DictReader(f, dialect=dialecte)
        manquantes = set(COLONNES_OBLIGATOIRES) - set(lecteur.fieldnames or [])
        if manquantes:
            sys.exit(f"ERREUR : colonnes obligatoires absentes : {', '.join(sorted(manquantes))}")
        return list(lecteur)


def appliquer(client: Client, c: ClientNettoye) -> None:
    client.nom_client = c.nom_client
    client.type_client = TypeClient.PROFESSIONNEL if c.professionnel else TypeClient.PARTICULIER
    client.adresse, client.voie = c.adresse, c.voie
    client.code_postal, client.ville = c.code_postal, c.ville
    client.telephone, client.digicode, client.notes_livraison = c.telephone, c.digicode, c.notes_livraison
    client.creneau_debut, client.creneau_fin = c.creneau_debut, c.creneau_fin
    client.est_immeuble, client.etage, client.ascenseur = c.est_immeuble, c.etage, c.ascenseur


def importer(chemin: Path) -> None:
    lignes = lire_csv(chemin)
    crees = maj = geo_reinit = avertis = 0
    rejets: list[tuple[int, str, str]] = []
    vus: set[str] = set()

    with SessionLocal() as db:
        for num, ligne in enumerate(lignes, start=2):          # ligne 1 = en-tête
            try:
                c = nettoyer_ligne(ligne)
            except LigneInvalide as e:
                rejets.append((num, (ligne.get("id_client") or "?").strip(), str(e)))
                continue
            if c.id_client in vus:
                rejets.append((num, c.id_client, "doublon dans le fichier"))
                continue
            vus.add(c.id_client)
            avertis += bool(c.avertissements)

            client = db.get(Client, c.id_client)
            if client is None:
                client = Client(id_client=c.id_client, source=SourceClient.IMPORT_CSV,
                                type_camion_requis=TypeCamion.NORMAL)
                appliquer(client, c)
                db.add(client)
                crees += 1
            else:
                if client.source == SourceClient.SIMULATION:
                    rejets.append((num, c.id_client, "identifiant réservé au jeu simulé"))
                    continue
                adresse_changee = client.adresse != c.adresse
                appliquer(client, c)   # type_camion_requis conservé (peut avoir été corrigé à la main)
                if adresse_changee:
                    client.latitude = client.longitude = client.geocode_precision = None
                    client.geocode_le = client.zone_id = None
                    geo_reinit += 1
                maj += 1

        # Traçabilité : l'import de données personnelles est une action sensible
        db.add(JournalSecurite(
            evenement="IMPORT_CSV", succes=True,
            details={"fichier": chemin.name, "lignes": len(lignes), "crees": crees,
                     "mis_a_jour": maj, "rejetes": len(rejets)},
        ))
        db.commit()   # tout ou rien : en cas d'erreur base, aucune ligne n'est écrite

    print(f"Fichier            : {chemin}")
    print(f"Lignes lues        : {len(lignes)}")
    print(f"Clients créés      : {crees}")
    print(f"Clients mis à jour : {maj} (dont {geo_reinit} à re-géocoder)")
    print(f"Avec avertissement : {avertis} (compléments d'adresse déplacés dans les notes chiffrées)")
    print(f"Lignes rejetées    : {len(rejets)}")
    for num, ident, motif in rejets:
        print(f"  - ligne {num} (id {ident}) : {motif}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import d'un CSV de tournées")
    parser.add_argument("fichier", nargs="?", default=FICHIER_DEFAUT)
    chemin = Path(parser.parse_args().fichier)
    if not chemin.exists():
        sys.exit(f"ERREUR : fichier introuvable : {chemin}")
    importer(chemin)
