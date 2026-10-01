"""Calcule les variables du modèle prédictif pour tous les clients et affiche leur répartition.

Usage :
    docker compose exec api python -m scripts.analyser_variables
    docker compose exec api python -m scripts.analyser_variables --csv /tmp/variables.csv

Compare les clients réels (import CSV) et simulés : le jeu simulé doit ressembler au réel pour que
le modèle entraîné dessus reste valable. Le CSV exporté ne contient ni identifiant, ni nom, ni texte
des notes : uniquement des variables (aucune donnée personnelle).
Aucune écriture en base.
"""
import argparse
import csv
from collections import Counter

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.ml.variables import COLONNES, MOTIFS_MENTIONS, variables_client
from app.models import Client
from app.models.enums import SourceClient


def pourcentage(n: int, total: int) -> str:
    return f"{100 * n / total:5.1f} %" if total else "    -  "


def main(args) -> None:
    reglages = get_settings()
    depot = (reglages.depot_latitude, reglages.depot_longitude)
    groupes: dict[str, list[dict]] = {"reel": [], "simule": []}
    with SessionLocal() as db:
        for c in db.scalars(select(Client).where(Client.actif.is_(True))):
            groupe = "simule" if c.source == SourceClient.SIMULATION else "reel"
            groupes[groupe].append(variables_client(c, depot))   # notes déchiffrées en mémoire

    reels, simules = groupes["reel"], groupes["simule"]
    print(f"\nClients actifs : {len(reels)} réels, {len(simules)} simulés\n")
    print(f"{'Variable':<28} {'Réels':>8} {'Simulés':>9}")
    oui_non = ["camion_hayon", "professionnel", "immeuble", "digicode_renseigne", *MOTIFS_MENTIONS]
    for nom in oui_non:
        print(f"{nom:<28} {pourcentage(sum(v[nom] for v in reels), len(reels)):>8} "
              f"{pourcentage(sum(v[nom] for v in simules), len(simules)):>9}")

    def moyenne(liste, nom):
        return sum(v[nom] for v in liste) / len(liste) if liste else 0

    print()
    for nom in ("etage", "etages_a_pied", "largeur_creneau_min", "distance_depot_km", "nb_mots_notes"):
        print(f"{'moyenne ' + nom:<28} {moyenne(reels, nom):>8.1f} {moyenne(simules, nom):>9.1f}")

    print("\nAscenseur (réels)  :", dict(Counter(v["ascenseur"] for v in reels)))
    print("Ascenseur (simulés):", dict(Counter(v["ascenseur"] for v in simules)))
    print("Zones (réels)      :", dict(Counter(v["zone"] for v in reels)))
    print(f"Variables par client : {len(COLONNES)}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            ecrivain = csv.DictWriter(f, fieldnames=["source", *COLONNES])
            ecrivain.writeheader()
            for groupe, lignes in groupes.items():
                for v in lignes:
                    ecrivain.writerow({"source": groupe, **v})
        print(f"\nExport : {args.csv} ({len(reels) + len(simules)} lignes, sans donnée personnelle)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Variables du modèle prédictif")
    parser.add_argument("--csv", help="chemin d'export dans le conteneur, ex. /tmp/variables.csv")
    main(parser.parse_args())
