"""Planification d'une journée complète dans une zone : segmentation hayon / normal,
tournées du matin, de l'après-midi et du soir, limite d'effectif, créneaux, budget < 10 s.

Usage :
    docker compose exec api python -m scripts.planifier_journee
    docker compose exec api python -m scripts.planifier_journee --zone "Première couronne" --nombre 40
    docker compose exec api python -m scripts.planifier_journee --journee-continue       # comparaison
    docker compose exec api python -m scripts.planifier_journee --details

Aucune écriture en base.
"""
import argparse
import random
import sys
from collections import Counter

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import Client, Vehicule, Zone
from app.models.enums import SourceClient, TypeCamion
from app.optim.probleme import en_heure
from app.services.planification import planifier_journee


def charger(zone_nom: str, nombre: int, part_hayon: float, graine: int):
    with SessionLocal() as db:
        zone = db.scalar(select(Zone).where(Zone.nom == zone_nom))
        if zone is None:
            sys.exit("Zone inconnue. Zones : " + ", ".join(db.scalars(select(Zone.nom))))
        flotte = {t.value: n for t, n in db.execute(
            select(Vehicule.type_camion, func.count()).where(Vehicule.actif.is_(True))
            .group_by(Vehicule.type_camion)).all()}

        def candidats(type_camion):
            return db.scalars(select(Client).where(
                Client.zone_id == zone.id, Client.latitude.is_not(None), Client.actif.is_(True),
                Client.source == SourceClient.SIMULATION, Client.type_camion_requis == type_camion,
            ).order_by(Client.id_client)).all()

        rng = random.Random(graine)  # tirage de simulation reproductible (graine), sans usage de sécurité  # nosec B311
        nb_hayon = round(nombre * part_hayon)
        hayon, normal = candidats(TypeCamion.HAYON), candidats(TypeCamion.NORMAL)
        if len(hayon) < nb_hayon or len(normal) < nombre - nb_hayon:
            sys.exit("Pas assez de clients dans cette zone pour ce tirage.")
        choisis = rng.sample(hayon, nb_hayon) + rng.sample(normal, nombre - nb_hayon)
    return sorted(choisis, key=lambda c: c.id_client), zone, flotte


def main(args) -> None:
    reglages = get_settings()
    clients, zone, flotte = charger(args.zone, args.nombre, args.part_hayon, args.graine)
    plan = planifier_journee(clients, reglages, zone.max_clients, flotte,
                             journee_continue=args.journee_continue)

    types = Counter(c.type_camion_requis.value for c in clients)
    print(f"\nZone « {zone.nom} » (limite {zone.max_clients} clients/tournée) - {len(clients)} clients : "
          f"{types['normal']} normal, {types['hayon']} hayon - flotte : {flotte}")
    print(f"Mode : {'journée continue' if args.journee_continue else 'tournées matin / après-midi / soirée'}\n")
    print(f"{'Tournée':<8} {'Camion':<7} {'Période':<11} {'Clients':>7} {'km':>7} "
          f"{'Départ':>7} {'Retour':>7} {'Attente':>8} {'Retards':>8}")
    for i, t in enumerate(plan.tournees, start=1):
        ev = t.evaluation
        print(f"{i:<8} {t.type_camion:<7} {t.periode:<11} {len(t.clients):>7} "
              f"{ev.distance_m / 1000:>7.1f} {en_heure(ev.depart_s):>7} {en_heure(ev.retour_depot_s):>7} "
              f"{ev.attente_totale_s // 60:>5} min {ev.nb_retards:>8}")
        if args.details:
            for rang, (c, p) in enumerate(zip(t.clients, ev.passages), start=1):
                etat = f"RETARD {p.retard_s // 60} min" if p.retard_s else "OK"
                print(f"         {rang:>2}. {c.id_client}  {c.creneau_debut:%H:%M}-{c.creneau_fin:%H:%M}"
                      f"  passage {en_heure(p.arrivee_s)}  {etat}")

    # Contrôle : aucun client hayon dans une tournée normale et inversement
    melanges = sum(any(c.type_camion_requis.value != t.type_camion for c in t.clients)
                   for t in plan.tournees)
    depassements = sum(len(t.clients) > zone.max_clients for t in plan.tournees)

    print(f"\nClients servis          : {plan.nb_servis} / {len(clients)}")
    if plan.a_reporter:
        print(f"À reporter              : {', '.join(c.id_client for c in plan.a_reporter)}")
    print(f"Distance totale         : {plan.distance_m / 1000:.1f} km")
    print(f"Créneaux non respectés  : {plan.nb_retards}")
    print(f"Attente cumulée         : {plan.attente_totale_s // 60} min")
    print(f"Véhicule inadapté       : {melanges} tournée(s)   (attendu : 0)")
    print(f"Limite d'effectif       : {depassements} dépassement(s)   (attendu : 0)")
    print(f"Temps de calcul total   : {plan.duree_calcul_s} s   (critère : < 10 s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Planification d'une journée")
    parser.add_argument("--zone", default="Lyon - Villeurbanne")
    parser.add_argument("--nombre", type=int, default=40)
    parser.add_argument("--part-hayon", type=float, default=0.2,
                        help="part de clients nécessitant un hayon dans le tirage")
    parser.add_argument("--graine", type=int, default=1)
    parser.add_argument("--journee-continue", action="store_true",
                        help="une seule tournée de 7 h à 22 h (comparaison)")
    parser.add_argument("--details", action="store_true", help="affiche chaque passage")
    main(parser.parse_args())
