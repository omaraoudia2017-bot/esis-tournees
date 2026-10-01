"""Génération d'un jeu de données simulé réaliste, à partir des distributions de l'échantillon réel.

Usage :
    docker compose exec api python -m scripts.generer_jeu_simule                    # 5000 clients
    docker compose exec api python -m scripts.generer_jeu_simule --clients 10000 --graine 7
    docker compose exec api python -m scripts.generer_jeu_simule --purger           # supprime le jeu
    docker compose exec api python -m scripts.generer_jeu_simule --notes-seulement  # refait les notes

Pourquoi : valider la montée en charge et entraîner les modèles sans dépendre de la base de
production, et disposer d'une démonstration sans aucune donnée personnelle réelle.

Ce qui est repris de l'échantillon (statistiquement, jamais recopié) :
  - positions : points tirés autour des clients réels géocodés (écart-type ~400 m) ;
  - créneaux horaires, profils d'accès (immeuble / étage / ascenseur), taux de digicodes,
    part de professionnels ;
  - fréquence de chaque type de consigne dans les notes (interphone, bâtiment, portail, appel...),
    mesurée avec les mêmes expressions régulières que le modèle prédictif (app/ml/variables.py).
Ce qui est inventé : noms, numéros et noms de rues, digicodes, notes. Identifiants : 9XXXXXXXXX.
"""
import argparse
import random
import sys
from decimal import Decimal

from sqlalchemy import delete, func, select

from app.db.session import SessionLocal
from app.ml.variables import extraire_mentions
from app.models import Client, Vehicule, ZoneCodePostal
from app.models.enums import PrecisionGeocodage, SourceClient, TypeCamion, TypeClient

PREFIXE_ID = "9"
PREFIXE_VEHICULE = "SIM-"
ECART_TYPE_KM = 0.4
KM_PAR_DEGRE_LAT, KM_PAR_DEGRE_LON = 111.0, 77.7   # à la latitude de Lyon
PART_HAYON_CHEZ_PROS = 0.5

PRENOMS = ["MARIE", "NATHALIE", "ISABELLE", "SYLVIE", "CATHERINE", "SANDRINE", "CELINE", "JULIE",
           "CAMILLE", "LAURA", "SOPHIE", "EMMA", "JEAN", "PIERRE", "MICHEL", "PHILIPPE", "NICOLAS",
           "THOMAS", "JULIEN", "ANTOINE", "LUCAS", "HUGO", "LOUIS", "PAUL", "KARIM", "YANIS",
           "INES", "SARAH", "LEA", "MANON", "AMINA", "NORA", "DAVID", "ALEXANDRE", "MAXIME"]
NOMS = ["MARTIN", "BERNARD", "THOMAS", "PETIT", "ROBERT", "RICHARD", "DURAND", "DUBOIS", "MOREAU",
        "LAURENT", "SIMON", "MICHEL", "LEFEBVRE", "LEROY", "ROUX", "DAVID", "BERTRAND", "MOREL",
        "FOURNIER", "GIRARD", "BONNET", "DUPONT", "LAMBERT", "FONTAINE", "ROUSSEAU", "VINCENT",
        "MULLER", "FAURE", "ANDRE", "MERCIER", "BLANC", "GUERIN", "BOYER", "GARNIER", "CHEVALIER"]
ACTIVITES = ["LOGISTIQUE", "CONSEIL", "BATIMENT", "RESTAURATION", "PHARMACIE", "BOULANGERIE",
             "IMPRIMERIE", "GARAGE", "FLEURS", "INFORMATIQUE", "CRECHE", "CABINET MEDICAL"]
TYPES_VOIE = ["RUE", "RUE", "RUE", "AVENUE", "CHEMIN", "BOULEVARD", "PLACE", "IMPASSE", "ALLEE",
              "ROUTE", "QUAI", "COURS"]
NOMS_VOIE = ["DE LA REPUBLIQUE", "VICTOR HUGO", "JEAN JAURES", "DES LILAS", "DU MOULIN", "PASTEUR",
             "DES ACACIAS", "DE LA GARE", "DU STADE", "DES ECOLES", "GAMBETTA", "DES TILLEULS",
             "DU PARC", "DE LA PAIX", "DES CERISIERS", "DE LA LIBERTE", "DU COMMERCE",
             "DES PEUPLIERS", "EMILE ZOLA", "DES ROSIERS", "DU CHATEAU", "DE LA FONTAINE"]
# Phrases inventées, rédigées comme les notes réelles (majuscules et accents absents).
# Chaque groupe déclenche UNE seule mention du modèle : vérifié par tests/test_jeu_simule.py.
CONSIGNES = {
    "mention_interphone": ["Sonner a l interphone au nom du client.",
                           "Sonner a l interphone puis prendre la porte de droite.",
                           "Bien sonner deux fois, sonnette peu audible."],
    "mention_batiment": ["BAT: {lettre} a gauche en entrant dans la cour.",
                         "Batiment {lettre}, porte vitree a droite.",
                         "Residence des {arbre}, bat {lettre}."],
    "mention_portail": ["Portail vert, le client ouvre a distance.",
                        "Passer par le portillon a cote du garage.",
                        "Portail electrique, attendre l ouverture complete."],
    "mention_appel": ["Appeler en arrivant devant la maison.",
                      "Merci d appeler dix minutes avant la livraison.",
                      "Contacter le client si absent, souvent au jardin."],
    "mention_digicode": ["Code porte a l entree principale.", "Digicode sur la porte en verre."],
    "mention_acces_difficile": ["Maison au fond de l impasse.",
                                "Stationnement difficile, se garer dans la rue adjacente.",
                                "Rue etroite, eviter de venir avec un gros vehicule."],
    "mention_patienter": ["Personne agee, patienter devant la porte."],
    "mention_accueil": ["Entreprise. S adresser a l accueil.",
                        "Deposer les colis a l accueil du rez de chaussee."],
    "mention_quai": ["Livraison sur palette par le QUAI a l arriere.",
                     "Acces QUAI par la rue de derriere."],
}
CONSIGNES_NEUTRES = ["Possibilite de stationner devant l allee pour la livraison.",
                     "Merci de deposer le colis devant la porte si absent.",
                     "Colis fragile, manipuler avec precaution.",
                     "Le client est disponible toute la matinee."]
ARBRES = ["TILLEULS", "ACACIAS", "PLATANES", "CEDRES", "ERABLES"]
MENTIONS_PROS = {"mention_quai", "mention_accueil"}     # tirées seulement pour les professionnels
TAUX_DEFAUT = {"mention_interphone": 0.26, "mention_batiment": 0.19, "mention_portail": 0.10,
               "mention_appel": 0.06, "mention_digicode": 0.06, "mention_acces_difficile": 0.05,
               "mention_patienter": 0.01, "mention_accueil": 0.13, "mention_quai": 0.07}
TAUX_NEUTRE = 0.35


def distributions(db) -> dict:
    """Mesure les distributions sur les clients réels (source = import CSV)."""
    ancres = db.execute(
        select(Client.latitude, Client.longitude, Client.code_postal, Client.ville)
        .where(Client.source != SourceClient.SIMULATION, Client.latitude.is_not(None),
               Client.geocode_precision.in_([PrecisionGeocodage.ADRESSE, PrecisionGeocodage.RUE]))
    ).all()
    if not ancres:
        sys.exit("ERREUR : aucun client réel géocodé. Lancez d'abord l'import puis le géocodage.")
    total = db.scalar(select(func.count()).where(Client.source != SourceClient.SIMULATION))
    return {
        "taux_consignes": taux_consignes(db),
        "ancres": [(float(la), float(lo), cp, v) for la, lo, cp, v in ancres],
        "creneaux": db.execute(
            select(Client.creneau_debut, Client.creneau_fin).where(
                Client.source != SourceClient.SIMULATION, Client.creneau_debut.is_not(None))
        ).all(),
        "acces": db.execute(
            select(Client.est_immeuble, Client.etage, Client.ascenseur)
            .where(Client.source != SourceClient.SIMULATION)
        ).all(),
        "taux_digicode": db.scalar(select(func.count()).where(
            Client.source != SourceClient.SIMULATION, Client.digicode.is_not(None))) / total,
        "taux_pro": db.scalar(select(func.count()).where(
            Client.source != SourceClient.SIMULATION,
            Client.type_client == TypeClient.PROFESSIONNEL)) / total,
    }


def taux_consignes(db) -> dict[str, float]:
    """Fréquence de chaque consigne dans les notes réelles (déchiffrées en mémoire seulement).
    Quai et accueil sont mesurés parmi les professionnels, les autres parmi tous les clients."""
    reels = db.execute(select(Client.notes_livraison, Client.type_client)
                       .where(Client.source == SourceClient.IMPORT_CSV)).all()
    pros = [n for n, t in reels if t == TypeClient.PROFESSIONNEL]
    taux = {}
    for nom in CONSIGNES:
        population = pros if nom in MENTIONS_PROS else [n for n, _ in reels]
        taux[nom] = (sum(extraire_mentions(n)[nom] for n in population) / len(population)
                     if population else TAUX_DEFAUT[nom])
        if nom in MENTIONS_PROS:      # échantillon de pros trop petit : on garde une valeur plancher
            taux[nom] = max(taux[nom], TAUX_DEFAUT[nom] / 2)
    return taux


def notes_pour(rng: random.Random, immeuble, etage, ascenseur, pro: bool,
               taux: dict[str, float] | None = None) -> str | None:
    taux = taux or TAUX_DEFAUT
    morceaux = []
    if immeuble and etage is not None:
        asc = {True: " AVEC ASC.", False: " SANS ASC."}.get(ascenseur, ".")
        morceaux.append(f"IMMEUBLE ETAGE: {etage}{asc}")
    for nom, phrases in CONSIGNES.items():
        if nom in MENTIONS_PROS and not pro:
            continue
        if rng.random() < taux[nom]:
            morceaux.append(rng.choice(phrases).format(lettre=rng.choice("ABCDE"),
                                                       arbre=rng.choice(ARBRES)))
    if rng.random() < TAUX_NEUTRE:
        morceaux.append(rng.choice(CONSIGNES_NEUTRES))
    return " ".join(morceaux) or None


def generer(nb: int, graine: int) -> None:
    rng = random.Random(graine)   # graine fixe : jeu reproductible d'une exécution à l'autre
    with SessionLocal() as db:
        deja = db.scalar(select(func.count()).where(Client.source == SourceClient.SIMULATION))
        if deja:
            sys.exit(f"{deja} clients simulés existent déjà. Utilisez --purger avant de régénérer.")

        d = distributions(db)
        zone_par_cp = dict(db.execute(select(ZoneCodePostal.code_postal, ZoneCodePostal.zone_id)).all())
        compteur = {"hayon": 0, "pro": 0, "digicode": 0}

        for n in range(1, nb + 1):
            lat0, lon0, cp, ville = rng.choice(d["ancres"])
            lat = lat0 + rng.gauss(0, ECART_TYPE_KM) / KM_PAR_DEGRE_LAT
            lon = lon0 + rng.gauss(0, ECART_TYPE_KM) / KM_PAR_DEGRE_LON
            pro = rng.random() < d["taux_pro"]
            hayon = pro and rng.random() < PART_HAYON_CHEZ_PROS
            immeuble, etage, ascenseur = rng.choice(d["acces"])
            debut, fin = rng.choice(d["creneaux"])
            digicode = None
            if rng.random() < d["taux_digicode"]:
                digicode = rng.choice([f"{rng.randint(1000, 9999)}",
                                       f"{rng.choice('ABC')}{rng.randint(1000, 9999)}"])

            nom = (f"SOCIETE {rng.choice(NOMS)} {rng.choice(ACTIVITES)}" if pro
                   else f"{rng.choice(NOMS)} {rng.choice(PRENOMS)}")
            voie = f"{rng.randint(1, 150)} {rng.choice(TYPES_VOIE)} {rng.choice(NOMS_VOIE)}"

            db.add(Client(
                id_client=f"{PREFIXE_ID}{n:09d}",
                nom_client=nom,
                type_client=TypeClient.PROFESSIONNEL if pro else TypeClient.PARTICULIER,
                adresse=f"{voie}, {cp} {ville}", voie=voie, code_postal=cp, ville=ville,
                latitude=Decimal(f"{lat:.6f}"), longitude=Decimal(f"{lon:.6f}"),
                geocode_precision=PrecisionGeocodage.SIMULEE,
                digicode=digicode,
                notes_livraison=notes_pour(rng, immeuble, etage, ascenseur, pro, d["taux_consignes"]),
                creneau_debut=debut, creneau_fin=fin,
                type_camion_requis=TypeCamion.HAYON if hayon else TypeCamion.NORMAL,
                est_immeuble=immeuble, etage=etage, ascenseur=ascenseur,
                zone_id=zone_par_cp.get(cp),
                source=SourceClient.SIMULATION,
            ))
            compteur["hayon"] += hayon
            compteur["pro"] += pro
            compteur["digicode"] += digicode is not None
            if n % 1000 == 0:
                db.flush()
                print(f"  {n}/{nb}")

        # Flotte simulée : 10 véhicules normaux et 4 à hayon (créés s'ils n'existent pas)
        existants = set(db.scalars(select(Vehicule.immatriculation)))
        flotte = [(f"{PREFIXE_VEHICULE}N{i:02d}", TypeCamion.NORMAL) for i in range(1, 11)]
        flotte += [(f"{PREFIXE_VEHICULE}H{i:02d}", TypeCamion.HAYON) for i in range(1, 5)]
        for immat, type_camion in flotte:
            if immat not in existants:
                db.add(Vehicule(immatriculation=immat, type_camion=type_camion))
        db.commit()

    print(f"{nb} clients simulés créés (graine {graine}) : {compteur['pro']} professionnels, "
          f"{compteur['hayon']} nécessitant un hayon, {compteur['digicode']} avec digicode.")
    print("14 véhicules simulés créés (10 normaux, 4 hayon).")


def refaire_notes(graine: int) -> None:
    """Retire au sort les notes des clients simulés existants, avec les fréquences mesurées sur les
    clients réels. Identifiants, positions, créneaux et tournées existantes sont conservés."""
    rng = random.Random(graine)
    with SessionLocal() as db:
        taux = taux_consignes(db)
        clients = db.scalars(select(Client).where(Client.source == SourceClient.SIMULATION)
                             .order_by(Client.id_client)).all()
        if not clients:
            sys.exit("Aucun client simulé : lancez d'abord la génération.")
        for c in clients:
            c.notes_livraison = notes_pour(rng, c.est_immeuble, c.etage, c.ascenseur,
                                           c.type_client == TypeClient.PROFESSIONNEL, taux)
        db.commit()
    print(f"Notes refaites pour {len(clients)} clients simulés (graine {graine}).")
    print("Fréquences utilisées : " + ", ".join(f"{k.removeprefix('mention_')} {v:.1%}"
                                                for k, v in taux.items()))


def purger() -> None:
    with SessionLocal() as db:
        nb = db.execute(delete(Client).where(Client.source == SourceClient.SIMULATION)).rowcount
        nv = db.execute(delete(Vehicule).where(
            Vehicule.immatriculation.startswith(PREFIXE_VEHICULE))).rowcount
        db.commit()
    print(f"Supprimés : {nb} clients simulés, {nv} véhicules simulés.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Jeu de données simulé")
    parser.add_argument("--clients", type=int, default=5000)
    parser.add_argument("--graine", type=int, default=42)
    parser.add_argument("--purger", action="store_true", help="supprime le jeu simulé existant")
    parser.add_argument("--notes-seulement", action="store_true",
                        help="refait uniquement les notes des clients simulés existants")
    args = parser.parse_args()
    if args.purger:
        purger()
    elif args.notes_seulement:
        refaire_notes(args.graine)
    else:
        generer(args.clients, args.graine)
