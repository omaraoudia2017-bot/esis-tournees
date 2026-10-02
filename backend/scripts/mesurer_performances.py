"""Test de performance sur le jeu simulé volumineux (5 000 clients) — rapport de tests du Jour 13.

Usage :
    docker compose exec api python -m scripts.mesurer_performances
    docker compose exec api python -m scripts.mesurer_performances --rapide        # moins de tailles

Mesures (aucune écriture en base) :
  1. une tournée de 10, 15 puis 20 clients : temps de calcul OR-Tools et qualité selon la limite
     de temps (critère du cahier des charges : moins de 10 s pour 20 clients) ;
  2. une journée entière de 60, 120 puis 200 clients (segmentation, plusieurs camions) : budget
     global de 9 s, clients servis, retards, contraintes respectées ;
  3. le modèle de temps de service sur les 5 000 clients simulés ;
  4. les requêtes de l'espace administrateur (recherche, tableau de bord sur 90 jours) ;
  5. le temps de réponse de l'API (/health, 50 appels).
"""
import argparse
import statistics
import time
import urllib.request

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import Client
from app.models.enums import SourceClient
from app.optim.cvrptw import optimiser_tournees
from app.services.admin import rechercher_clients, tableau_de_bord
from app.services.planification import construire_probleme, estimer_temps_service_s, planifier_journee
from scripts.planifier_journee import charger

ZONE = "Lyon - Villeurbanne"


def chrono(fonction, *args, **kwargs):
    debut = time.perf_counter()
    resultat = fonction(*args, **kwargs)
    return resultat, time.perf_counter() - debut


def titre(texte: str) -> None:
    print(f"\n{texte}\n{'-' * len(texte)}")


def tournee_unique(reglages, tailles, limites) -> None:
    titre("1. Une tournée (1 camion normal, créneaux, temps de service prédits)")
    print(f"{'Clients':>7} {'Limite':>7} {'Calcul':>8} {'km':>7} {'Retards':>8} {'Non servis':>11}")
    clients_zone, _, _ = charger(ZONE, max(tailles), 0.0, 3)
    for n in tailles:
        clients = clients_zone[:n]
        for limite in limites:
            probleme = construire_probleme(clients, reglages, 20, nb_vehicules=1,
                                           types_vehicules=["normal"])
            res, duree = chrono(optimiser_tournees, probleme, limite)
            ev = res.evaluations[0]
            print(f"{n:>7} {limite:>5} s {duree:>6.2f} s {ev.distance_m / 1000:>7.1f} "
                  f"{ev.nb_retards:>8} {len(res.non_servis):>11}")


def journee(reglages, tailles) -> None:
    titre(f"2. Journée complète, zone « {ZONE} » (20 % de palettes, matin / après-midi / soirée)")
    print(f"{'Clients':>7} {'Tournées':>9} {'Calcul':>8} {'Servis':>8} {'Retards':>8} "
          f"{'Camion inadapté':>16} {'Limite dépassée':>16}")
    for n in tailles:
        clients, zone, flotte = charger(ZONE, n, 0.2, 1)
        plan = planifier_journee(clients, reglages, zone.max_clients, flotte)
        melanges = sum(any(c.type_camion_requis.value != t.type_camion for c in t.clients)
                       for t in plan.tournees)
        depassements = sum(len(t.clients) > zone.max_clients for t in plan.tournees)
        print(f"{n:>7} {len(plan.tournees):>9} {plan.duree_calcul_s:>6.2f} s "
              f"{plan.nb_servis:>4}/{n:<3} {plan.nb_retards:>8} {melanges:>16} {depassements:>16}")
    print(f"Flotte disponible : {flotte}")


def modele(reglages) -> None:
    titre("3. Modèle de temps de service sur tout le jeu simulé")
    with SessionLocal() as db:
        clients = db.scalars(select(Client).where(Client.source == SourceClient.SIMULATION)).all()
    (temps, source), duree = chrono(estimer_temps_service_s, clients, reglages)
    print(f"{len(clients)} clients estimés en {duree:.2f} s (source : {source}), "
          f"médiane {statistics.median(temps) / 60:.1f} min")


def requetes(reglages) -> None:
    titre("4. Requêtes de l'espace administrateur")
    with SessionLocal() as db:
        lignes, d1 = chrono(rechercher_clients, db, "RUE", False, 500)
        td, d2 = chrono(tableau_de_bord, db, reglages, 90)
    print(f"Recherche « RUE » sur 5 000+ clients ({len(lignes)} résultats, limite 500) : {d1 * 1000:.0f} ms")
    print(f"Tableau de bord 90 jours ({td.nb_tournees} tournées, {td.nb_livraisons} livraisons) : "
          f"{d2 * 1000:.0f} ms")


def api(nb: int = 50) -> None:
    titre("5. Temps de réponse de l'API (GET /health, base comprise)")
    durees = []
    for _ in range(nb):
        debut = time.perf_counter()
        with urllib.request.urlopen("http://localhost:8000/health", timeout=5) as r:  # URL fixe locale  # nosec B310
            r.read()
        durees.append((time.perf_counter() - debut) * 1000)
    durees.sort()
    print(f"{nb} appels : médiane {statistics.median(durees):.1f} ms, "
          f"95e centile {durees[int(0.95 * nb) - 1]:.1f} ms, max {durees[-1]:.1f} ms")


def main(args) -> None:
    reglages = get_settings()
    print(f"Limite de calcul configurée (bouton Optimiser) : {reglages.optim_temps_limite_s} s ; "
          f"budget journée : {reglages.optim_budget_total_s} s")
    tournee_unique(reglages, [10, 20] if args.rapide else [10, 15, 20],
                   [1, reglages.optim_temps_limite_s] if args.rapide else [1, 2, reglages.optim_temps_limite_s])
    journee(reglages, [60, 120] if args.rapide else [60, 120, 200])
    modele(reglages)
    requetes(reglages)
    api()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test de performance (jeu simulé volumineux)")
    parser.add_argument("--rapide", action="store_true")
    main(parser.parse_args())
