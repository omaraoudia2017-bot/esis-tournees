"""Planification CVRPTW d'une ou plusieurs tournées dans une zone.

Usage :
    docker compose exec api python -m scripts.planifier_tournee
    docker compose exec api python -m scripts.planifier_tournee --zone "Première couronne" --nombre 30
    docker compose exec api python -m scripts.planifier_tournee --source reel --zone "Lyon - Villeurbanne" --nombre 10

Prend en compte : créneaux horaires, temps de service (10 / 20 min), limite d'effectif de la zone.
Le nombre de véhicules est calculé automatiquement (nombre de clients / limite de la zone).
Compare avec l'ordre du Jour 4 (trajet le plus court sans tenir compte des créneaux).
Aucune écriture en base.
"""
import argparse
import math
import random
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import Client, Zone
from app.models.enums import SourceClient, TypeCamion
from app.optim.cvrptw import optimiser_tournees
from app.optim.probleme import en_heure, evaluer_parcours
from app.optim.solver import optimiser_ordre
from app.services.planification import construire_probleme


def charger(source: str, nom_zone: str, nombre: int, graine: int) -> tuple[list[Client], Zone]:
    with SessionLocal() as db:
        zone = db.scalar(select(Zone).where(Zone.nom == nom_zone))
        if zone is None:
            noms = ", ".join(db.scalars(select(Zone.nom)))
            sys.exit(f"Zone inconnue. Zones disponibles : {noms}")
        requete = select(Client).where(Client.zone_id == zone.id, Client.latitude.is_not(None),
                                       Client.actif.is_(True),
                                       Client.type_camion_requis == TypeCamion.NORMAL)
        requete = requete.where(Client.source == SourceClient.SIMULATION) if source == "simulation" \
            else requete.where(Client.source != SourceClient.SIMULATION)
        candidats = db.scalars(requete.order_by(Client.id_client)).all()
    if len(candidats) < nombre:
        sys.exit(f"Seulement {len(candidats)} clients disponibles dans cette zone.")
    return sorted(random.Random(graine).sample(candidats, nombre), key=lambda c: c.id_client), zone  # tirage de simulation reproductible (graine), sans usage de sécurité  # nosec B311


def main(args) -> None:
    reglages = get_settings()
    clients, zone = charger(args.source, args.zone, args.nombre, args.graine)
    nb_vehicules = args.vehicules or math.ceil(len(clients) / zone.max_clients)
    probleme = construire_probleme(clients, reglages, zone.max_clients, nb_vehicules)

    # Référence Jour 4 : chemin le plus court, sans créneaux, un seul véhicule
    ordre_j4 = optimiser_ordre(probleme.matrice, args.temps).ordre
    ref = evaluer_parcours(probleme, ordre_j4)

    res = optimiser_tournees(probleme, args.temps)

    print(f"\nZone « {zone.nom} » : limite {zone.max_clients} clients par tournée - "
          f"{len(clients)} clients ({args.source}) - {nb_vehicules} véhicule(s) - "
          f"départ {reglages.heure_debut_tournee} de {reglages.depot_nom}")
    for v, (ordre, ev) in enumerate(zip(res.tournees, res.evaluations), start=1):
        if not ordre:
            continue
        print(f"\n--- Tournée {v} : {len(ordre)} clients, {ev.distance_m / 1000:.1f} km, "
              f"retour au dépôt {en_heure(ev.retour_depot_s)} ---")
        print(f"{'N°':>3}  {'Client':<10}  {'Créneau':<11}  {'Passage':<7}  {'Attente':>7}  État")
        for rang, passage in enumerate(ev.passages, start=1):
            c = probleme.clients[passage.noeud - 1]
            etat = f"RETARD {passage.retard_s // 60} min" if passage.retard_s else "OK"
            print(f"{rang:>3}  {c.identifiant:<10}  {en_heure(c.debut_s)}-{en_heure(c.fin_s)}  "
                  f"{en_heure(passage.arrivee_s):<7}  {passage.attente_s // 60:>4} min  {etat}")

    if res.non_servis:
        ids = ", ".join(probleme.clients[k - 1].identifiant for k in res.non_servis)
        print(f"\nÀ reporter sur une autre tournée ({len(res.non_servis)}) : {ids}")

    print("\n                          Distance   Créneaux non respectés   Retard cumulé")
    print(f"Jour 4 (sans créneaux) : {ref.distance_m / 1000:7.1f} km   {ref.nb_retards:>12} / {len(clients):<9}"
          f"{ref.retard_total_s // 60:>8} min")
    print(f"Jour 5 (CVRPTW)        : {res.distance_m / 1000:7.1f} km   {res.nb_retards:>12} / "
          f"{len(clients) - len(res.non_servis):<9}{res.retard_total_s // 60:>8} min")
    print(f"\nCalcul : {res.duree_calcul_s} s - statut : {res.statut}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Planification CVRPTW")
    parser.add_argument("--source", choices=["reel", "simulation"], default="simulation")
    parser.add_argument("--zone", default="Lyon - Villeurbanne")
    parser.add_argument("--nombre", type=int, default=20)
    parser.add_argument("--vehicules", type=int, help="par défaut : calculé selon la limite de la zone")
    parser.add_argument("--graine", type=int, default=1)
    parser.add_argument("--temps", type=int, default=get_settings().optim_temps_limite_s)
    main(parser.parse_args())
