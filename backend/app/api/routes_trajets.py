"""Routes du livreur pour sa « journée de trajets » (jours sans tournée attribuée) :
saisie des étapes, réglage du départ et du retour, optimisation, pointage « Fait »."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import exiger_role
from app.api.routes_tournees import geocodeur
from app.core.config import get_settings
from app.db.session import get_db
from app.models import EtapeTrajet, JourneeTrajets, Utilisateur
from app.models.enums import RoleUtilisateur
from app.schemas.tournee import Point
from app.schemas.trajets import (DemandeEtape, DemandeReglages, EtapeVue, JourneeVue, ReponseEtape,
                                 ReponseTrajets)
from app.services import trajets as service
from app.services.domicile import position_domicile
from app.services.geocodage import Geocodeur
from app.services.journal import contexte_depuis, journaliser

router = APIRouter(tags=["Journée de trajets du livreur"])
livreur_connecte = exiger_role(RoleUtilisateur.LIVREUR)


def _http(e: service.ErreurTrajets) -> HTTPException:
    return HTTPException(e.statut, e.message)


def vue_etape(e: EtapeTrajet) -> EtapeVue:
    p = service.point_etape(e)
    return EtapeVue(id=e.id, ordre=e.ordre, libelle=e.libelle, adresse=e.adresse, latitude=p.latitude,
                    longitude=p.longitude, position_approximative=e.position_approximative,
                    rdv_debut=e.rdv_debut, rdv_fin=e.rdv_fin, duree_min=e.duree_min,
                    heure_prevue=e.heure_prevue, faite_le=e.faite_le)


def _point_vue(nom: str, u: Utilisateur) -> Point:
    """Le domicile n'est renvoyé qu'au livreur lui-même ; sans domicile, retour au dépôt."""
    reglages = get_settings()
    p = service.point(nom, u, reglages)
    domicile = nom == "domicile" and position_domicile(u) is not None
    return Point(nom="Domicile" if domicile else "Dépôt", latitude=p.latitude, longitude=p.longitude)


def vue_journee(db: Session, u: Utilisateur, journee: JourneeTrajets | None) -> JourneeVue:
    reglages = get_settings()
    jour = service.aujourdhui(reglages)
    commun = dict(date_journee=jour, tournee_attribuee=service.a_une_tournee(db, u, jour),
                  domicile_disponible=position_domicile(u) is not None,
                  max_etapes=reglages.trajets_max_etapes, duree_defaut_min=reglages.trajets_duree_defaut_min)
    if journee is None:                     # rien de saisi : vue par défaut, aucune ligne créée
        return JourneeVue(**commun, point_depart=_point_vue("depot", u), point_arrivee=_point_vue("depot", u))
    return JourneeVue(
        **commun, depart=journee.depart, retour=journee.retour, heure_depart=journee.heure_depart,
        optimisee_le=journee.optimisee_le,
        distance_km=float(journee.distance_km) if journee.distance_km is not None else None,
        duree_min=journee.duree_min,
        point_depart=_point_vue(journee.depart, u), point_arrivee=_point_vue(journee.retour, u),
        etapes=[vue_etape(e) for e in service.etapes_de(db, journee)])


def _etape_autorisee(request: Request, db: Session, etape_id: int, u: Utilisateur) -> EtapeTrajet:
    etape = service.etape_du_livreur(db, etape_id, u)
    if etape is None:
        # Même réponse qu'une étape inexistante ; la tentative est tracée
        journaliser(db, "ACCES_REFUSE", False, contexte_depuis(request), u.id, u.identifiant,
                    route=request.url.path, ressource="etape_trajet", ressource_id=etape_id)
        db.commit()
        raise HTTPException(404, "Étape introuvable.")
    return etape


@router.get("/trajets/du-jour", response_model=JourneeVue)
def journee_du_jour(u: Utilisateur = Depends(livreur_connecte), db: Session = Depends(get_db)):
    """Étapes du jour du livreur connecté (et de lui seul)."""
    return vue_journee(db, u, service.journee_du_jour(db, u, get_settings()))


@router.post("/trajets/du-jour/etapes", response_model=ReponseEtape, status_code=201)
def ajouter_etape(demande: DemandeEtape, request: Request, u: Utilisateur = Depends(livreur_connecte),
                  db: Session = Depends(get_db), geo: Geocodeur = Depends(geocodeur)):
    """Nouvelle étape (libellé, adresse, rendez-vous facultatif, durée). Refusé si une tournée est
    attribuée aujourd'hui. Appuyer ensuite sur « Optimiser » pour l'intégrer."""
    try:
        etape, avertissement = service.ajouter_etape(db, u, service.NouvelleEtape(**demande.model_dump()),
                                                     geo, get_settings(), contexte_depuis(request))
    except service.ErreurTrajets as e:
        raise _http(e) from None
    return ReponseEtape(etape=vue_etape(etape), avertissement=avertissement)


@router.delete("/trajets/etapes/{etape_id}", status_code=204)
def supprimer_etape(etape_id: int, request: Request, u: Utilisateur = Depends(livreur_connecte),
                    db: Session = Depends(get_db)):
    service.supprimer_etape(db, _etape_autorisee(request, db, etape_id, u), u, contexte_depuis(request))
    return Response(status_code=204)


@router.post("/trajets/etapes/{etape_id}/faite", response_model=EtapeVue)
def marquer_faite(etape_id: int, request: Request, u: Utilisateur = Depends(livreur_connecte),
                  db: Session = Depends(get_db)):
    try:
        return vue_etape(service.marquer(db, _etape_autorisee(request, db, etape_id, u), True))
    except service.ErreurTrajets as e:
        raise _http(e) from None


@router.post("/trajets/etapes/{etape_id}/a-faire", response_model=EtapeVue)
def marquer_a_faire(etape_id: int, request: Request, u: Utilisateur = Depends(livreur_connecte),
                    db: Session = Depends(get_db)):
    """Annule un « Fait » cliqué par erreur."""
    try:
        return vue_etape(service.marquer(db, _etape_autorisee(request, db, etape_id, u), False))
    except service.ErreurTrajets as e:
        raise _http(e) from None


@router.put("/trajets/du-jour/reglages", response_model=JourneeVue)
def regler(demande: DemandeReglages, u: Utilisateur = Depends(livreur_connecte), db: Session = Depends(get_db)):
    """Départ et retour : dépôt, ou domicile (livreur interne ayant renseigné son adresse)."""
    try:
        journee = service.regler(db, u, get_settings(), demande.depart, demande.retour, demande.heure_depart)
    except service.ErreurTrajets as e:
        raise _http(e) from None
    return vue_journee(db, u, journee)


@router.post("/trajets/du-jour/optimiser", response_model=ReponseTrajets)
def optimiser(request: Request, u: Utilisateur = Depends(livreur_connecte), db: Session = Depends(get_db)):
    """Ordre de passage respectant les rendez-vous (même solveur OR-Tools que les tournées)."""
    try:
        r = service.optimiser(db, u, get_settings(), contexte_depuis(request))
    except service.ErreurTrajets as e:
        raise _http(e) from None
    return ReponseTrajets(**r.__dict__)
