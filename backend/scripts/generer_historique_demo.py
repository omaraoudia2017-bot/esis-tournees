"""Historique de tournées SIMULÉES (jours passés), pour illustrer le tableau de bord en démonstration.

Usage :
    docker compose exec api python -m scripts.generer_historique_demo                 # 10 jours, 4 livreurs
    docker compose exec api python -m scripts.generer_historique_demo --jours 20 --livreurs 5
    docker compose exec api python -m scripts.generer_historique_demo --purger        # tout retirer

Pour chaque jour ouvré passé et chaque livreur fictif (comptes « sim.livreurN », désactivés : personne
ne peut s'y connecter) : une tournée de clients SIMULÉS, optimisée par OR-Tools avec les temps
prédits, puis « exécutée » : temps sur place tirés selon les hypothèses du modèle (app/ml/temps_service),
retard reporté sur les clients suivants, environ 6 % d'absents et 2 % de refus.

Ces pointages portent sur des clients simulés : ils ne sont JAMAIS utilisés pour entraîner le modèle
(scripts.entrainer_modele ne retient que les pointages sur de vrais clients).
"""
import argparse
import random
import sys
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.security import generer_mdp, hacher_mdp
from app.db.session import SessionLocal
from app.ml.temps_service import temps_simule_min
from app.ml.variables import variables_client
from app.models import Client, Livraison, Tournee, Utilisateur, Vehicule, Zone
from app.models.enums import RoleUtilisateur, SourceClient, StatutLivraison, StatutTournee
from app.optim.cvrptw import optimiser_tournees
from app.optim.segmentation import periode_de
from app.services.journal import journaliser
from app.services.planification import (construire_probleme, estimer_temps_service_s, periodes,
                                         vers_client_tournee)
from app.services.tournees import aujourdhui

PREFIXE = "sim.livreur"
# Camions des livreurs fictifs : 3 normaux, puis 1 hayon (livreur.demo garde SIM-N01)
VEHICULES = ["SIM-N02", "SIM-N03", "SIM-N04", "SIM-H01", "SIM-N05", "SIM-H02"]


def livreurs_fictifs(db, nombre: int) -> list[tuple[Utilisateur, Vehicule]]:
    resultat = []
    for i in range(1, nombre + 1):
        vehicule = db.scalar(select(Vehicule).where(Vehicule.immatriculation == VEHICULES[i - 1]))
        if vehicule is None:
            sys.exit(f"Véhicule {VEHICULES[i - 1]} absent : lancez d'abord scripts.generer_jeu_simule.")
        u = db.scalar(select(Utilisateur).where(Utilisateur.identifiant == f"{PREFIXE}{i}"))
        if u is None:
            u = Utilisateur(identifiant=f"{PREFIXE}{i}", nom="SIMULE", prenom=f"Livreur {i}",
                            role=RoleUtilisateur.LIVREUR, mot_de_passe_hash=hacher_mdp(generer_mdp()),
                            actif=False, vehicule_id=vehicule.id)
            db.add(u)
            db.flush()
        resultat.append((u, vehicule))
    return resultat


def purger(db) -> int:
    ids = list(db.scalars(select(Utilisateur.id).where(Utilisateur.identifiant.startswith(PREFIXE))))
    if not ids:
        return 0
    n = db.execute(delete(Tournee).where(Tournee.livreur_id.in_(ids))).rowcount   # livraisons en cascade
    db.execute(delete(Utilisateur).where(Utilisateur.id.in_(ids)))
    return n


def executer(tournee: Tournee, clients: list[Client], reglages, rng: random.Random,
             depot) -> tuple[dict[str, Livraison], int]:
    """Optimise la tournée puis simule son exécution. Retourne (livraisons, nombre de reportés)."""
    fuseau = ZoneInfo(reglages.fuseau_horaire)
    minuit = datetime.combine(tournee.date_tournee, time(0), tzinfo=fuseau)
    predits, _ = estimer_temps_service_s(clients, reglages)
    probleme = construire_probleme(clients, reglages, tournee.max_clients, nb_vehicules=1,
                                   depart_s=tournee.heure_depart.hour * 3600,
                                   fin_s=tournee.heure_fin.hour * 3600, temps_service_s=predits,
                                   types_vehicules=[clients[0].type_camion_requis.value])
    res = optimiser_tournees(probleme, 1)
    ordre, evaluation = res.tournees[0], res.evaluations[0]
    livraisons = {}
    retard_s = 0                                   # temps perdu, reporté sur les clients suivants
    for rang, (noeud, passage) in enumerate(zip(ordre, evaluation.passages), start=1):
        client = clients[noeud - 1]
        prevu = minuit + timedelta(seconds=passage.arrivee_s)
        debut = prevu + timedelta(seconds=max(0, retard_s))
        tirage = rng.random()
        statut = (StatutLivraison.ABSENT if tirage < 0.06 else
                  StatutLivraison.REFUSE if tirage < 0.08 else StatutLivraison.LIVRE)
        minutes = (temps_simule_min(variables_client(client, depot), rng) if statut == StatutLivraison.LIVRE
                   else rng.uniform(2, 4))
        fin = debut + timedelta(minutes=minutes)
        # Plus long que prévu : retard reporté sur la suite ; plus court : le livreur garde l'horaire prévu
        retard_s = max(0, retard_s + minutes * 60 - predits[noeud - 1])
        livraisons[client.id_client] = Livraison(
            tournee_id=tournee.id, client_id=client.id_client, ordre_passage=rang, statut=statut,
            heure_arrivee_prevue=prevu, heure_debut_livraison=debut, heure_fin_livraison=fin,
            temps_service_predit_s=predits[noeud - 1])
    reportes = [clients[k - 1] for k in res.non_servis]
    for rang, client in enumerate(reportes, start=len(ordre) + 1):
        livraisons[client.id_client] = Livraison(tournee_id=tournee.id, client_id=client.id_client,
                                                 ordre_passage=rang, statut=StatutLivraison.REPORTE)
    tournee.statut = StatutTournee.TERMINEE
    tournee.optimisee_le = minuit + timedelta(seconds=tournee.heure_depart.hour * 3600 - 600)
    tournee.distance_totale_km = round(evaluation.distance_m / 1000, 2)
    tournee.duree_estimee_min = max(0, (evaluation.retour_depot_s - tournee.heure_depart.hour * 3600) // 60)
    return livraisons, len(reportes)


def main(args) -> None:
    reglages = get_settings()
    rng = random.Random(args.graine)  # tirage de simulation reproductible (graine), sans usage de sécurité  # nosec B311
    depot = (reglages.depot_latitude, reglages.depot_longitude)
    toutes_periodes = periodes(reglages)
    with SessionLocal() as db:
        anciennes = purger(db)
        if args.purger:
            db.commit()
            print(f"Historique de démonstration supprimé : {anciennes} tournée(s).")
            return
        livreurs = livreurs_fictifs(db, args.livreurs)
        zones = list(db.scalars(select(Zone).order_by(Zone.max_clients.desc())))
        simules = db.scalars(select(Client).where(Client.source == SourceClient.SIMULATION,
                                                  Client.actif.is_(True),
                                                  Client.latitude.is_not(None))).all()
        if not simules:
            sys.exit("Aucun client simulé : lancez d'abord scripts.generer_jeu_simule.")
        periode_client = {c.id_client: periode_de(vers_client_tournee(c, reglages), toutes_periodes).nom
                          for c in simules}

        jours, jour = [], aujourdhui(reglages) - timedelta(days=1)
        while len(jours) < args.jours:
            if jour.weekday() < 5:
                jours.append(jour)
            jour -= timedelta(days=1)

        nb_tournees = nb_livraisons = nb_reportes = 0
        for jour in sorted(jours):
            utilises: set[str] = set()
            for i, (livreur, vehicule) in enumerate(livreurs):
                zone = zones[(i + jour.toordinal()) % len(zones)]
                periode = rng.choice(["matin", "matin", "apres-midi"])
                candidats = [c for c in simules if c.zone_id == zone.id and c.id_client not in utilises
                             and c.type_camion_requis == vehicule.type_camion
                             and periode_client[c.id_client] == periode]
                nombre = min(len(candidats), rng.randint(max(zone.max_clients - 6, 8), zone.max_clients))
                if nombre < 5:
                    continue
                clients = rng.sample(candidats, nombre)
                utilises.update(c.id_client for c in clients)
                p = next(x for x in toutes_periodes if x.nom == periode)
                tournee = Tournee(date_tournee=jour, livreur_id=livreur.id, vehicule_id=vehicule.id,
                                  zone_id=zone.id, max_clients=zone.max_clients,
                                  heure_depart=time(p.depart_s // 3600), heure_fin=time(min(p.fin_s // 3600, 23)))
                db.add(tournee)
                db.flush()
                livraisons, reportes = executer(tournee, clients, reglages, rng, depot)
                db.add_all(livraisons.values())
                db.flush()
                nb_tournees += 1
                nb_livraisons += len(livraisons)
                nb_reportes += reportes
            print(f"  {jour:%d/%m} : {nb_tournees} tournées au total")
        journaliser(db, "DEMO_HISTORIQUE", True, None, tournees=nb_tournees, jours=args.jours,
                    source="cli")
        db.commit()
    print(f"\nHistorique simulé : {nb_tournees} tournées, {nb_livraisons} livraisons "
          f"({nb_reportes} à reporter) sur {args.jours} jours ouvrés, {args.livreurs} livreurs fictifs "
          f"(comptes désactivés). Anciennes tournées de démonstration remplacées : {anciennes}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Historique de tournées simulées (démonstration)")
    parser.add_argument("--jours", type=int, default=10, help="jours ouvrés passés (défaut 10)")
    parser.add_argument("--livreurs", type=int, default=4, choices=range(1, len(VEHICULES) + 1))
    parser.add_argument("--graine", type=int, default=12)
    parser.add_argument("--purger", action="store_true", help="supprime l'historique de démonstration")
    main(parser.parse_args())
