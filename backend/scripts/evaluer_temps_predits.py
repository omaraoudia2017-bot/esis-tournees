"""Mesure l'apport des temps de service prédits dans la planification des tournées.

Usage :
    docker compose exec api python -m scripts.evaluer_temps_predits
    docker compose exec api python -m scripts.evaluer_temps_predits --tournees 20 --nombre 15

Pour chaque tournée tirée au hasard (clients simulés) :
  1. on planifie deux fois : avec les valeurs fixes (10 / 20 min) puis avec les temps prédits ;
  2. on « rejoue » chaque plan avec des temps de service « réels » simulés (mêmes hypothèses que
     l'historique d'entraînement, avec un aléa) ;
  3. on compare les heures de passage annoncées aux heures réellement obtenues.
Critère : des heures de passage annoncées plus justes, donc des clients mieux informés.
Aucune écriture en base.
"""
import argparse
import random
import sys
from dataclasses import replace
from statistics import mean

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.ml.temps_service import temps_simule_min
from app.ml.variables import variables_client
from app.models import Client, Zone
from app.models.enums import SourceClient, TypeCamion
from app.optim.cvrptw import optimiser_tournees
from app.optim.probleme import evaluer_parcours
from app.services.planification import (construire_probleme, estimer_temps_service_s,
                                        temps_service_theorique_s)


def jouer(clients, temps_plan_s, temps_reels_s, reglages, max_clients, temps_limite):
    p = construire_probleme(clients, reglages, max_clients, 1, temps_service_s=temps_plan_s)
    ordre = optimiser_tournees(p, temps_limite).tournees[0]
    prevu = evaluer_parcours(p, ordre)
    reel_p = replace(p, clients=[replace(c, temps_service_s=t) for c, t in zip(p.clients, temps_reels_s)])
    reel = evaluer_parcours(reel_p, ordre)
    ecarts = [abs(a.arrivee_s - b.arrivee_s) / 60 for a, b in zip(prevu.passages, reel.passages)]
    return {
        "ecart_passage_min": mean(ecarts) if ecarts else 0.0,
        "ecart_retour_min": abs(prevu.retour_depot_s - reel.retour_depot_s) / 60,
        "retards_annonces": prevu.nb_retards,
        "retards_reels": reel.nb_retards,
        "servis": len(ordre),
    }


def main(args) -> None:
    reglages = get_settings()
    depot = (reglages.depot_latitude, reglages.depot_longitude)
    with SessionLocal() as db:
        zone = db.scalar(select(Zone).where(Zone.nom == args.zone))
        if zone is None:
            sys.exit("Zone inconnue.")
        candidats = db.scalars(select(Client).where(
            Client.zone_id == zone.id, Client.source == SourceClient.SIMULATION,
            Client.type_camion_requis == TypeCamion.NORMAL, Client.latitude.is_not(None))).all()

    _, source = estimer_temps_service_s(candidats[:1], reglages)
    if source != "modele":
        sys.exit("Aucun modèle de temps de service actif : lancez d'abord scripts.entrainer_modele.")

    rng = random.Random(args.graine)  # tirage de simulation reproductible (graine), sans usage de sécurité  # nosec B311
    resultats = {"fixe": [], "modele": []}
    for k in range(args.tournees):
        clients = rng.sample(candidats, args.nombre)
        reels = [int(temps_simule_min(variables_client(c, depot), rng) * 60) for c in clients]
        fixes = [temps_service_theorique_s(c, reglages) for c in clients]
        predits, _ = estimer_temps_service_s(clients, reglages)
        resultats["fixe"].append(jouer(clients, fixes, reels, reglages, zone.max_clients, args.temps))
        resultats["modele"].append(jouer(clients, predits, reels, reglages, zone.max_clients, args.temps))
        print(f"  tournée {k + 1}/{args.tournees}")

    print(f"\n{args.tournees} tournées de {args.nombre} clients, zone « {zone.nom} »\n")
    print(f"{'Indicateur (moyenne par tournée)':<44} {'Valeurs fixes':>14} {'Temps prédits':>14}")
    lignes = [("Écart heure annoncée / réelle (min)", "ecart_passage_min", "{:.1f}"),
              ("Écart heure de retour au dépôt (min)", "ecart_retour_min", "{:.1f}"),
              ("Créneaux dépassés annoncés", "retards_annonces", "{:.2f}"),
              ("Créneaux dépassés réellement", "retards_reels", "{:.2f}")]
    for libelle, cle, fmt in lignes:
        f = mean(r[cle] for r in resultats["fixe"])
        m = mean(r[cle] for r in resultats["modele"])
        print(f"{libelle:<44} {fmt.format(f):>14} {fmt.format(m):>14}")
    print("\nLecture : plus l'écart est faible, plus l'heure communiquée au client est fiable ;")
    print("un retard réel non annoncé est un client qui attend sans avoir été prévenu.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Apport des temps prédits dans la planification")
    parser.add_argument("--zone", default="Lyon - Villeurbanne")
    parser.add_argument("--tournees", type=int, default=10)
    parser.add_argument("--nombre", type=int, default=15)
    parser.add_argument("--graine", type=int, default=3)
    parser.add_argument("--temps", type=float, default=1.0, help="temps de calcul par tournée (s)")
    main(parser.parse_args())
