"""Attribue une tournée du jour à un livreur, à partir des clients SIMULÉS (démonstration).

En production, les tournées viendront de l'import CSV de l'espace administrateur.
Les clients sont enregistrés dans un ordre quelconque (« liste brute ») : c'est le bouton
« Optimiser » de l'écran livreur qui calcule l'ordre de passage.

Usage :
    docker compose exec api python -m scripts.attribuer_tournee --livreur livreur.demo
    docker compose exec api python -m scripts.attribuer_tournee --livreur livreur.demo --periode apres-midi --nombre 12
    docker compose exec api python -m scripts.attribuer_tournee --livreur livreur.demo --remplacer

Règles appliquées : clients de la zone choisie, du type de camion du véhicule du livreur,
dont le créneau tombe dans la période ; nombre limité à la limite d'effectif de la zone.
"""
import argparse
import random
import sys
from datetime import date, time

from sqlalchemy import delete, func, select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import Client, Livraison, Tournee, Utilisateur, Vehicule, Zone
from app.models.enums import RoleUtilisateur, SourceClient
from app.optim.segmentation import periode_de
from app.services.journal import journaliser
from app.services.planification import periodes, temps_service_theorique_s, vers_client_tournee
from app.services.tournees import aujourdhui


def main(args) -> None:
    reglages = get_settings()
    jour = date.fromisoformat(args.date) if args.date else aujourdhui(reglages)
    toutes = periodes(reglages) + periodes(reglages, journee_continue=True)
    periode = next(p for p in toutes if p.nom == args.periode)

    with SessionLocal() as db:
        livreur = db.scalar(select(Utilisateur).where(
            func.lower(Utilisateur.identifiant) == args.livreur.lower()))
        if livreur is None or livreur.role != RoleUtilisateur.LIVREUR:
            sys.exit(f"Livreur introuvable : {args.livreur}")
        vehicule = db.get(Vehicule, livreur.vehicule_id) if livreur.vehicule_id else None
        if vehicule is None:
            sys.exit("Ce livreur n'a pas de véhicule attribué.")
        zone = db.scalar(select(Zone).where(Zone.nom == args.zone))
        if zone is None:
            sys.exit("Zone inconnue. Zones : " + ", ".join(db.scalars(select(Zone.nom))))
        nombre = args.nombre
        if nombre > zone.max_clients:
            sys.exit(f"Limite d'effectif de la zone : {zone.max_clients} clients maximum.")

        existante = db.scalar(select(Tournee).where(Tournee.livreur_id == livreur.id,
                                                    Tournee.date_tournee == jour))
        if existante is not None:
            if not args.remplacer:
                sys.exit(f"{livreur.identifiant} a déjà une tournée le {jour:%d/%m/%Y} "
                         "(ajoutez --remplacer pour la recréer).")
            db.execute(delete(Tournee).where(Tournee.id == existante.id))   # livraisons en cascade
            db.flush()

        candidats = db.scalars(select(Client).where(
            Client.zone_id == zone.id, Client.actif.is_(True), Client.latitude.is_not(None),
            Client.source == SourceClient.SIMULATION,
            Client.type_camion_requis == vehicule.type_camion,
        ).order_by(Client.id_client)).all()
        if periode.nom == "journee":
            dans_periode = candidats
        else:
            dans_periode = [c for c in candidats if periode_de(
                vers_client_tournee(c, reglages), periodes(reglages)).nom == periode.nom]
        if len(dans_periode) < nombre:
            sys.exit(f"Seulement {len(dans_periode)} clients disponibles pour ce tirage.")
        choisis = random.Random(args.graine).sample(dans_periode, nombre)   # ordre quelconque

        tournee = Tournee(date_tournee=jour, livreur_id=livreur.id, vehicule_id=vehicule.id,
                          zone_id=zone.id, max_clients=zone.max_clients,
                          heure_depart=_heure(periode.depart_s), heure_fin=_heure(periode.fin_s))
        db.add(tournee)
        db.flush()
        for c in choisis:
            db.add(Livraison(tournee_id=tournee.id, client_id=c.id_client,
                             temps_service_predit_s=temps_service_theorique_s(c, reglages)))
        journaliser(db, "ADMIN_ATTRIBUTION_TOURNEE", True, None, livreur.id, livreur.identifiant,
                    tournee_id=tournee.id, nb_clients=nombre, source="cli")
        db.commit()

    print(f"Tournée {tournee.id} du {jour:%d/%m/%Y} attribuée à {livreur.identifiant} : "
          f"{nombre} clients, zone « {zone.nom} » (limite {zone.max_clients}), "
          f"camion {vehicule.immatriculation} ({vehicule.type_camion.value}), "
          f"période {periode.nom} {_heure(periode.depart_s):%H:%M}-{_heure(periode.fin_s):%H:%M}.")


def _heure(secondes: int) -> time:
    return time(min(secondes // 3600, 23), secondes % 3600 // 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Attribution d'une tournée du jour (démonstration)")
    parser.add_argument("--livreur", required=True)
    parser.add_argument("--zone", default="Lyon - Villeurbanne")
    parser.add_argument("--nombre", type=int, default=12,
                        help="nombre de clients (au plus la limite d'effectif de la zone)")
    parser.add_argument("--periode", default="matin",
                        choices=["matin", "apres-midi", "soiree", "journee"])
    parser.add_argument("--date", help="AAAA-MM-JJ, par défaut aujourd'hui")
    parser.add_argument("--graine", type=int, default=7)
    parser.add_argument("--remplacer", action="store_true",
                        help="supprime la tournée existante de ce jour (pointages compris)")
    main(parser.parse_args())
