"""Entraîne et évalue le détecteur de connexions anormales (Isolation Forest), puis l'enregistre.

Usage :
    docker compose exec api python -m scripts.entrainer_anomalies
    docker compose exec api python -m scripts.entrainer_anomalies --contamination 0.01 --sans-sauvegarde
    docker compose exec api python -m scripts.entrainer_anomalies --details   # raisons, connexions réelles

Entraînement : connexions normales simulées (80 %) + connexions réelles du journal de sécurité.
Évaluation :
  - fausses alertes sur les 20 % de connexions normales mises de côté (à garder bas) ;
  - détection sur des connexions suspectes simulées (nuit, IP inconnue, échecs, code de secours).
Aucune adresse IP ni aucun navigateur n'entre dans le modèle : seulement « déjà vu ou non ».
"""
import argparse
import random
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.ml.anomalies import (DetecteurAnomalies, attaques_simulees, entrainer, historique_simule,
                              raisons, sauvegarder, taux_alertes)
from app.services.anomalies import connexions_reelles_detaillees


def main(args) -> None:
    reglages = get_settings()
    normales = historique_simule(args.comptes, args.jours, args.graine, reglages.fuseau_horaire)
    random.Random(args.graine).shuffle(normales)
    coupure = int(len(normales) * 0.8)
    apprentissage, controle = normales[:coupure], normales[coupure:]
    with SessionLocal() as db:
        detaillees = connexions_reelles_detaillees(db, reglages.fuseau_horaire)
    reelles = [v for _, _, v in detaillees]

    modele = entrainer(apprentissage + reelles, args.contamination, args.graine)
    attaques = attaques_simulees(graine=args.graine + 1)

    print(f"\nConnexions d'entraînement : {len(apprentissage)} simulées + {len(reelles)} réelles "
          f"(journal de sécurité)")
    print(f"Contamination (part attendue d'anomalies) : {args.contamination:.0%}\n")
    print(f"Fausses alertes sur {len(controle)} connexions normales de contrôle : "
          f"{taux_alertes(modele, controle):6.1%}")
    print(f"Détection sur {len(attaques)} connexions suspectes simulées       : "
          f"{taux_alertes(modele, attaques):6.1%}")
    print(f"Alertes sur les {len(reelles)} connexions réelles du journal       : "
          f"{taux_alertes(modele, reelles):6.1%}")

    if args.details:
        print("\nConnexions réelles (aucune adresse IP affichée) :")
        verdicts = modele.predict([[v[c] for c in DetecteurAnomalies.colonnes()] for v in reelles]) \
            if reelles else []
        for (compte, instant, v), verdict in zip(detaillees, verdicts):
            local = instant.astimezone(ZoneInfo(reglages.fuseau_horaire))
            etat = "ALERTE" if verdict == -1 else "normale"
            detail = ", ".join(raisons(v)) if verdict == -1 else ""
            print(f"  {local:%d/%m %H:%M}  {compte:<18} {etat:<8} {detail}")

    if args.sans_sauvegarde:
        print("\nModèle non enregistré (--sans-sauvegarde).")
        return
    sauvegarder(modele, reglages.modele_anomalies, contamination=args.contamination,
                nb_simulees=len(apprentissage), nb_reelles=len(reelles))
    print(f"\nModèle enregistré : {reglages.modele_anomalies}. Chaque nouvelle connexion est "
          "désormais analysée ; les alertes apparaissent dans le journal (CONNEXION_ANORMALE).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Détection des connexions anormales")
    parser.add_argument("--comptes", type=int, default=30)
    parser.add_argument("--jours", type=int, default=60)
    parser.add_argument("--contamination", type=float, default=0.02)
    parser.add_argument("--graine", type=int, default=42)
    parser.add_argument("--sans-sauvegarde", action="store_true")
    parser.add_argument("--details", action="store_true",
                        help="verdict et raisons pour chaque connexion réelle du journal")
    main(parser.parse_args())
