"""Entraîne et évalue le modèle Random Forest du temps de service, puis l'enregistre.

Usage :
    docker compose exec api python -m scripts.entrainer_modele
    docker compose exec api python -m scripts.entrainer_modele --passages 40000 --graine 7
    docker compose exec api python -m scripts.entrainer_modele --sans-sauvegarde

Jeu d'entraînement :
  - historique simulé : livraisons tirées parmi les clients simulés, temps calculé selon les
    hypothèses de app/ml/temps_service.py (HYPOTHESES) avec un aléa ;
  - pointages réels : livraisons terminées « livré » dont la durée est plausible (1 à 90 min).
    Les pointages de démonstration (quelques secondes) sont écartés.
Aucun identifiant ni texte n'entre dans le jeu : uniquement les variables (anonymisation).
Critère du cahier des charges : erreur moyenne (MAE) du modèle < MAE des valeurs fixes 10 / 20 min.
"""
import argparse
import time

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.ml.temps_service import HYPOTHESES, entrainer, historique_simule, sauvegarder
from app.ml.variables import variables_client
from app.models import Client, Livraison
from app.models.enums import SourceClient, StatutLivraison

DUREE_PLAUSIBLE_S = (60, 90 * 60)


def charger(depot):
    with SessionLocal() as db:
        simules = [variables_client(c, depot) for c in db.scalars(
            select(Client).where(Client.source == SourceClient.SIMULATION, Client.actif.is_(True)))]
        pointages = db.execute(
            select(Client, Livraison.temps_service_reel_s)
            .join(Livraison, Livraison.client_id == Client.id_client)
            .where(Livraison.statut == StatutLivraison.LIVRE,
                   Livraison.temps_service_reel_s.is_not(None))).all()
    retenus = [(variables_client(c, depot), s / 60) for c, s in pointages
               if DUREE_PLAUSIBLE_S[0] <= s <= DUREE_PLAUSIBLE_S[1]]
    return simules, retenus, len(pointages) - len(retenus)


def main(args) -> None:
    reglages = get_settings()
    depot = (reglages.depot_latitude, reglages.depot_longitude)
    simules, reels, ecartes = charger(depot)
    if not simules:
        raise SystemExit("Aucun client simulé : lancez d'abord scripts.generer_jeu_simule.")

    X, y = historique_simule(simules, args.passages, args.graine)
    X += [v for v, _ in reels]
    y += [m for _, m in reels]

    debut = time.perf_counter()
    modele, rapport = entrainer(X, y, args.graine, args.arbres)
    duree = time.perf_counter() - debut

    print(f"\nJeu d'entraînement : {len(X)} livraisons = {args.passages} simulées "
          f"+ {len(reels)} pointages réels ({ecartes} pointages écartés : durée < 1 min ou > 90 min)")
    print(f"Apprentissage : {rapport.nb_entrainement} lignes, test : {rapport.nb_test} lignes, "
          f"{args.arbres} arbres, {duree:.1f} s\n")
    print(f"Erreur moyenne (MAE) valeurs fixes 10 / 20 min : {rapport.mae_fixe_min:5.2f} min")
    print(f"Erreur moyenne (MAE) Random Forest            : {rapport.mae_modele_min:5.2f} min")
    print(f"Gain                                          : {rapport.gain_pct:5.1f} %")
    print(f"R² du modèle                                  : {rapport.r2_modele:5.3f}")
    critere = "RESPECTÉ" if rapport.mae_modele_min < rapport.mae_fixe_min else "NON RESPECTÉ"
    print(f"Critère du cahier des charges (MAE modèle < MAE fixe) : {critere}\n")

    print("Variables les plus utiles au modèle :")
    for nom, poids in rapport.importances[:10]:
        print(f"  {nom:<26} {poids:6.1%}  {'#' * round(poids * 60)}")

    if args.sans_sauvegarde:
        print("\nModèle non enregistré (--sans-sauvegarde).")
        return
    sauvegarder(modele, rapport, reglages.modele_temps_service, graine=args.graine,
                nb_simules=args.passages, nb_reels=len(reels), hypotheses=HYPOTHESES)
    print(f"\nModèle enregistré : {reglages.modele_temps_service} (dossier backend/modeles, hors Git)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Entraînement du modèle de temps de service")
    parser.add_argument("--passages", type=int, default=20000, help="livraisons simulées")
    parser.add_argument("--graine", type=int, default=42)
    parser.add_argument("--arbres", type=int, default=200)
    parser.add_argument("--sans-sauvegarde", action="store_true")
    main(parser.parse_args())
