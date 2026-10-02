"""Gestion des comptes en ligne de commande (les mêmes actions existent dans l'espace administrateur).

Usage :
    docker compose exec api python -m scripts.gerer_utilisateurs creer --identifiant admin.omar \
        --nom AOUDIA --prenom Omar --role admin
    docker compose exec api python -m scripts.gerer_utilisateurs creer --identifiant livreur.demo \
        --nom DEMO --prenom Livreur --role livreur --vehicule SIM-N01 --generer-mdp
    docker compose exec api python -m scripts.gerer_utilisateurs lister
    docker compose exec api python -m scripts.gerer_utilisateurs deverrouiller --identifiant livreur.demo
    docker compose exec api python -m scripts.gerer_utilisateurs reinitialiser-2fa --identifiant livreur.demo
    docker compose exec api python -m scripts.gerer_utilisateurs desactiver --identifiant livreur.demo
    docker compose exec api python -m scripts.gerer_utilisateurs reinitialiser-mdp --identifiant admin.omar

Chaque action est tracée dans le journal de sécurité.
"""
import argparse
import getpass
import sys
from datetime import datetime, timezone

from sqlalchemy import delete, func, select

from app.core.security import generer_mdp, hacher_mdp, valider_politique_mdp
from app.db.session import SessionLocal
from app.models import CodeSecours, Utilisateur, Vehicule
from app.models.enums import RoleUtilisateur
from app.services.journal import journaliser


def demander_mdp() -> str:
    while True:
        mdp = getpass.getpass("Mot de passe (12 caractères min., majuscules, minuscules, chiffres) : ")
        erreurs = valider_politique_mdp(mdp)
        if erreurs:
            print("Refusé, il faut : " + ", ".join(erreurs))
            continue
        if getpass.getpass("Confirmation : ") != mdp:
            print("Les deux saisies diffèrent.")
            continue
        return mdp


def trouver(db, identifiant: str) -> Utilisateur:
    u = db.scalar(select(Utilisateur).where(func.lower(Utilisateur.identifiant) == identifiant.lower()))
    if u is None:
        sys.exit(f"Compte introuvable : {identifiant}")
    return u


def creer(args) -> None:
    ident = args.identifiant.strip().lower()
    with SessionLocal() as db:
        if db.scalar(select(Utilisateur).where(func.lower(Utilisateur.identifiant) == ident)):
            sys.exit(f"L'identifiant {ident} existe déjà.")
        vehicule_id = None
        if args.vehicule:
            vehicule = db.scalar(select(Vehicule).where(Vehicule.immatriculation == args.vehicule))
            if vehicule is None:
                sys.exit(f"Véhicule introuvable : {args.vehicule}")
            vehicule_id = vehicule.id
        mdp = generer_mdp() if args.generer_mdp else demander_mdp()
        u = Utilisateur(identifiant=ident, nom=args.nom.upper(), prenom=args.prenom,
                        role=RoleUtilisateur(args.role), mot_de_passe_hash=hacher_mdp(mdp),
                        vehicule_id=vehicule_id)
        db.add(u)
        db.flush()
        journaliser(db, "ADMIN_CREATION_COMPTE", True, None, u.id, ident, role=args.role, source="cli")
        db.commit()
    print(f"Compte créé : {ident} ({args.role}). La 2FA sera activée à la première connexion.")
    if args.generer_mdp:
        print(f"Mot de passe initial (affiché une seule fois) : {mdp}")


def lister(_args) -> None:
    with SessionLocal() as db:
        comptes = db.scalars(select(Utilisateur).order_by(Utilisateur.role, Utilisateur.identifiant)).all()
    if not comptes:
        print("Aucun compte.")
    for u in comptes:
        etat = "actif" if u.actif else "désactivé"
        if u.verrouille_jusqua:
            etat += f", verrouillé jusqu'à {u.verrouille_jusqua:%H:%M}"
        print(f"- {u.identifiant:<20} {u.role.value:<8} 2FA={'oui' if u.totp_active else 'non':<3} {etat}")


def modifier(args, action: str) -> None:
    with SessionLocal() as db:
        u = trouver(db, args.identifiant)
        if action == "deverrouiller":
            u.verrouille_jusqua, u.tentatives_echec = None, 0
        elif action == "reinitialiser-2fa":       # téléphone perdu : nouvel enrôlement à la connexion
            u.totp_secret, u.totp_active, u.totp_dernier_pas = None, False, None
            db.execute(delete(CodeSecours).where(CodeSecours.utilisateur_id == u.id))
        elif action == "desactiver":
            u.actif = False
            u.sessions_invalidees_le = datetime.now(timezone.utc)
        elif action == "reinitialiser-mdp":       # mot de passe oublié (y compris d'un administrateur)
            mdp = generer_mdp() if args.generer_mdp else demander_mdp()
            u.mot_de_passe_hash = hacher_mdp(mdp)
            u.verrouille_jusqua, u.tentatives_echec = None, 0
            u.sessions_invalidees_le = datetime.now(timezone.utc)   # anciennes sessions coupées
            if args.generer_mdp:
                print(f"Nouveau mot de passe (affiché une seule fois) : {mdp}")
        journaliser(db, f"ADMIN_{action.upper().replace('-', '_')}", True, None, u.id,
                    u.identifiant, source="cli")
        db.commit()
    print(f"{action} : {args.identifiant} OK")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gestion des comptes ESIS")
    sous = parser.add_subparsers(dest="commande", required=True)

    p = sous.add_parser("creer")
    p.add_argument("--identifiant", required=True)
    p.add_argument("--nom", required=True)
    p.add_argument("--prenom", required=True)
    p.add_argument("--role", choices=[r.value for r in RoleUtilisateur], default="livreur")
    p.add_argument("--vehicule", help="immatriculation, ex. SIM-N01")
    p.add_argument("--generer-mdp", action="store_true", help="génère un mot de passe robuste")
    sous.add_parser("lister")
    for nom in ("deverrouiller", "reinitialiser-2fa", "desactiver"):
        sous.add_parser(nom).add_argument("--identifiant", required=True)
    p = sous.add_parser("reinitialiser-mdp", help="mot de passe oublié (2FA conservée)")
    p.add_argument("--identifiant", required=True)
    p.add_argument("--generer-mdp", action="store_true", help="génère un mot de passe robuste")

    args = parser.parse_args()
    if args.commande == "creer":
        creer(args)
    elif args.commande == "lister":
        lister(args)
    else:
        modifier(args, args.commande)
