"""Routes réservées aux administrateurs (l'espace complet arrive au Jour 12)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import exiger_role
from app.db.session import get_db
from app.models import JournalSecurite
from app.models.enums import RoleUtilisateur
from app.schemas.auth import EvenementJournal

router = APIRouter(prefix="/admin", tags=["Administration"],
                   dependencies=[Depends(exiger_role(RoleUtilisateur.ADMIN))])


@router.get("/journal-securite", response_model=list[EvenementJournal])
def journal_securite(limite: int = Query(50, ge=1, le=500),
                     evenement: str | None = Query(None, pattern=r"^[A-Z0-9_]{1,50}$"),
                     db: Session = Depends(get_db)):
    """Dernières entrées du journal de sécurité (connexions, échecs, verrouillages, alertes...).
    `evenement` filtre sur un type, par exemple CONNEXION_ANORMALE."""
    requete = select(JournalSecurite)
    if evenement:
        requete = requete.where(JournalSecurite.evenement == evenement)
    lignes = db.scalars(requete.order_by(JournalSecurite.id.desc()).limit(limite))
    return [EvenementJournal(horodatage=l.horodatage, evenement=l.evenement, succes=l.succes,
                             utilisateur_id=l.utilisateur_id, identifiant_saisi=l.identifiant_saisi,
                             adresse_ip=str(l.adresse_ip) if l.adresse_ip else None,
                             details=l.details) for l in lignes]
