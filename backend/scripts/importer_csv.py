"""Import et nettoyage d'un fichier de tournées au format tournees_esis.csv (ligne de commande).

Usage :
    docker compose exec api python -m scripts.importer_csv                       # fichier par défaut
    docker compose exec api python -m scripts.importer_csv /data/raw/autre.csv

Même traitement que l'import de l'espace administrateur (app/services/import_csv.py).
Idempotent : relancer l'import met à jour les clients existants (clé = id_client).
Le géocodage se fait ensuite avec scripts.geocoder_clients (ou depuis l'espace administrateur).
"""
import argparse
import sys
from pathlib import Path

from app.db.session import SessionLocal
from app.services.import_csv import ErreurImport, importer_lignes, lire_texte
from app.services.journal import journaliser

FICHIER_DEFAUT = "/data/raw/tournees_esis.csv"


def importer(chemin: Path) -> None:
    try:
        lignes = lire_texte(chemin.read_text(encoding="utf-8-sig"))
    except ErreurImport as e:
        sys.exit(f"ERREUR : {e}")

    with SessionLocal() as db:
        bilan = importer_lignes(db, lignes)
        # Traçabilité : l'import de données personnelles est une action sensible
        journaliser(db, "IMPORT_CSV", True, None, fichier=chemin.name, lignes=bilan.lignes,
                    crees=bilan.crees, mis_a_jour=bilan.mis_a_jour, rejetes=len(bilan.rejets),
                    source="cli")
        db.commit()   # tout ou rien : en cas d'erreur base, aucune ligne n'est écrite

    print(f"Fichier            : {chemin}")
    print(f"Lignes lues        : {bilan.lignes}")
    print(f"Clients créés      : {bilan.crees}")
    print(f"Clients mis à jour : {bilan.mis_a_jour} (dont {bilan.a_regeocoder} à re-géocoder)")
    print(f"Avec avertissement : {bilan.avec_avertissement} (compléments d'adresse déplacés dans "
          "les notes chiffrées)")
    print(f"Lignes rejetées    : {len(bilan.rejets)}")
    for r in bilan.rejets:
        print(f"  - ligne {r.ligne} (id {r.id_client}) : {r.motif}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import d'un CSV de tournées")
    parser.add_argument("fichier", nargs="?", default=FICHIER_DEFAUT)
    chemin = Path(parser.parse_args().fichier)
    if not chemin.exists():
        sys.exit(f"ERREUR : fichier introuvable : {chemin}")
    importer(chemin)
