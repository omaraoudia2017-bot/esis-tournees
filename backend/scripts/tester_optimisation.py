"""Démonstration du premier modèle OR-Tools : ordre de passage d'une tournée.

Usage :
    docker compose exec api python -m scripts.tester_optimisation
    docker compose exec api python -m scripts.tester_optimisation --source simulation --nombre 20
    docker compose exec api python -m scripts.tester_optimisation --zone "Périurbain et rural" --nombre 10

Compare l'ordre « naïf » (ordre de saisie, celui utilisé aujourd'hui) à l'ordre optimisé.
Aucune écriture en base.
"""
import argparse
import random
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import Client, Zone
from app.models.enums import SourceClient
from app.optim.matrice import Point, construire_matrice, longueur_parcours
from app.optim.solver import optimiser_ordre


def choisir_clients(source: str, zone: str | None, nombre: int, graine: int) -> list[Client]:
    with SessionLocal() as db:
        requete = select(Client).where(Client.latitude.is_not(None), Client.actif.is_(True))
        requete = requete.where(Client.source == SourceClient.SIMULATION) if source == "simulation" \
            else requete.where(Client.source != SourceClient.SIMULATION)
        if zone:
            requete = requete.join(Zone, Zone.id == Client.zone_id).where(Zone.nom == zone)
        candidats = db.scalars(requete.order_by(Client.id_client)).all()
    if len(candidats) < nombre:
        sys.exit(f"Seulement {len(candidats)} clients disponibles pour ces critères.")
    tirage = random.Random(graine).sample(candidats, nombre)
    return sorted(tirage, key=lambda c: c.id_client)      # ordre de saisie = ordre naïf


def main(args) -> None:
    reglages = get_settings()
    clients = choisir_clients(args.source, args.zone, args.nombre, args.graine)
    depot = Point(reglages.depot_latitude, reglages.depot_longitude)
    points = [depot] + [Point(float(c.latitude), float(c.longitude)) for c in clients]
    matrice = construire_matrice(points, reglages.coefficient_routier, reglages.vitesse_moyenne_kmh)

    naif = list(range(1, len(clients) + 1))
    dist_naif = longueur_parcours(matrice.distances_m, naif, reglages.retour_depot)
    duree_naif = longueur_parcours(matrice.durees_s, naif, reglages.retour_depot)
    res = optimiser_ordre(matrice, args.temps, reglages.retour_depot)

    print(f"\nTournée de {len(clients)} clients ({args.source}"
          f"{', zone ' + args.zone if args.zone else ''}) - départ : {reglages.depot_nom}\n")
    print(f"{'N°':>3}  {'Client':<10}  {'Créneau':<11}  {'km cumulés':>10}  Adresse")
    precedent, cumul = 0, 0
    for rang, noeud in enumerate(res.ordre, start=1):
        c = clients[noeud - 1]
        cumul += matrice.distances_m[precedent][noeud]
        creneau = f"{c.creneau_debut:%H:%M}-{c.creneau_fin:%H:%M}" if c.creneau_debut else "-"
        print(f"{rang:>3}  {c.id_client:<10}  {creneau:<11}  {cumul / 1000:>10.1f}  {c.adresse[:45]}")
        precedent = noeud

    gain = 100 * (dist_naif - res.distance_m) / dist_naif if dist_naif else 0
    print(f"\nOrdre de saisie : {dist_naif / 1000:6.1f} km  {duree_naif / 60:5.0f} min de trajet")
    print(f"Ordre optimisé  : {res.distance_m / 1000:6.1f} km  {res.duree_trajet_s / 60:5.0f} min de trajet")
    print(f"Gain            : {gain:5.1f} %   (calcul : {res.duree_calcul_s} s, statut : {res.statut})")
    print("\nNB : créneaux horaires et temps de service non encore pris en compte (Jour 5).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test du solveur OR-Tools")
    parser.add_argument("--source", choices=["reel", "simulation"], default="reel")
    parser.add_argument("--zone", help='ex. "Lyon - Villeurbanne"')
    parser.add_argument("--nombre", type=int, default=20)
    parser.add_argument("--graine", type=int, default=1)
    parser.add_argument("--temps", type=int, default=get_settings().optim_temps_limite_s,
                        help="limite de calcul en secondes")
    main(parser.parse_args())
