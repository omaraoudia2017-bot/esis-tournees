"""Remarques sur une adresse, laissées par le livreur pour les prochaines livraisons.

Règles :
  - un livreur ne peut écrire une remarque que sur un client de SA tournée du jour ;
  - il ne peut supprimer que ses propres remarques ; l'administrateur peut supprimer toutes les
    remarques (contenu inadapté, demande RGPD) ;
  - le texte est chiffré en base et n'est jamais recopié dans le journal (seulement l'identifiant) ;
  - RGPD / CNIL (zones de commentaires libres) : l'écran rappelle de n'écrire que des
    informations utiles à la livraison, objectives, sans jugement sur la personne.
"""
from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import RemarqueClient, Utilisateur
from app.services.journal import ContexteRequete, journaliser

LONGUEUR_MIN, LONGUEUR_MAX = 3, 300


class ErreurRemarque(Exception):
    def __init__(self, statut: int, message: str):
        super().__init__(message)
        self.statut, self.message = statut, message


def remarques_par_client(db: Session, ids_clients: list[str], nombre: int) -> dict[str, list[RemarqueClient]]:
    """Les `nombre` remarques les plus récentes de chaque client (une seule requête)."""
    resultat: dict[str, list[RemarqueClient]] = defaultdict(list)
    if not ids_clients:
        return resultat
    lignes = db.scalars(select(RemarqueClient)
                        .where(RemarqueClient.client_id.in_(set(ids_clients)))
                        .order_by(RemarqueClient.cree_le.desc(), RemarqueClient.id.desc())).all()
    for r in lignes:
        if len(resultat[r.client_id]) < nombre:
            resultat[r.client_id].append(r)
    return resultat


def ajouter_remarque(db: Session, client_id: str, auteur: Utilisateur, texte: str,
                     ctx: ContexteRequete | None) -> RemarqueClient:
    texte = " ".join((texte or "").split())          # espaces et retours à la ligne superflus
    if not LONGUEUR_MIN <= len(texte) <= LONGUEUR_MAX:
        raise ErreurRemarque(422, f"La remarque doit faire entre {LONGUEUR_MIN} et {LONGUEUR_MAX} caractères.")
    remarque = RemarqueClient(client_id=client_id, auteur_id=auteur.id, texte=texte)
    db.add(remarque)
    db.flush()
    journaliser(db, "REMARQUE_AJOUTEE", True, ctx, auteur.id, auteur.identifiant,
                client_id=client_id, remarque_id=remarque.id)
    db.commit()
    db.refresh(remarque)
    return remarque


def supprimer_remarque(db: Session, remarque_id: int, utilisateur: Utilisateur,
                       ctx: ContexteRequete | None, admin: bool = False) -> None:
    """Le livreur ne voit pas les remarques des autres comme supprimables : 404 identique
    qu'elle n'existe pas ou qu'elle appartienne à un collègue."""
    remarque = db.get(RemarqueClient, remarque_id)
    if remarque is None or (not admin and remarque.auteur_id != utilisateur.id):
        raise ErreurRemarque(404, "Remarque introuvable.")
    client_id = remarque.client_id
    db.delete(remarque)
    journaliser(db, "REMARQUE_SUPPRIMEE", True, ctx, utilisateur.id, utilisateur.identifiant,
                client_id=client_id, remarque_id=remarque_id, par_admin=admin)
    db.commit()


def toutes_les_remarques(db: Session, client_id: str) -> list[RemarqueClient]:
    return db.scalars(select(RemarqueClient).where(RemarqueClient.client_id == client_id)
                      .order_by(RemarqueClient.cree_le.desc(), RemarqueClient.id.desc())).all()


def effacer_remarques_client(db: Session, client_id: str) -> int:
    """Anonymisation RGPD : toutes les remarques du client disparaissent (l'appelant fait le commit)."""
    return db.execute(delete(RemarqueClient).where(RemarqueClient.client_id == client_id)).rowcount
