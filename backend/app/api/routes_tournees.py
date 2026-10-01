"""Routes du livreur : tournée du jour, bouton « Optimiser », pointage Démarrer / Terminer,
ajout d'une adresse en cours de tournée."""
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import exiger_role
from app.core.config import get_settings
from app.db.session import get_db
from app.models import Client, Livraison, Tournee, Utilisateur, Vehicule, Zone
from app.models.enums import RoleUtilisateur, StatutLivraison
from app.optim.probleme import en_heure
from app.schemas.tournee import (ClientVue, DemandeAjoutClient, DemandeFin, LivraisonVue, Point,
                                 ReponseAjout, ReponseOptimisation, TourneeVue)
from app.services import tournees as service
from app.services.geocodage import Geocodeur
from app.services.journal import contexte_depuis, journaliser

router = APIRouter(tags=["Tournée du livreur"])
livreur_connecte = exiger_role(RoleUtilisateur.LIVREUR)


@lru_cache
def geocodeur() -> Geocodeur:
    """Un seul géocodeur pour toute l'API : son limiteur garantit 1 requête/s vers Nominatim.
    Remplacé par un faux géocodeur dans les tests (aucun appel réseau)."""
    return Geocodeur(get_settings().nominatim_user_agent)


def _http(erreur: service.ErreurTournee) -> HTTPException:
    return HTTPException(erreur.statut, erreur.message)


def vue_livraison(l: Livraison, c: Client) -> LivraisonVue:
    return LivraisonVue(
        id=l.id, ordre_passage=l.ordre_passage, statut=l.statut.value,
        ajout_a_la_volee=l.ajout_a_la_volee, heure_arrivee_prevue=l.heure_arrivee_prevue,
        heure_debut_livraison=l.heure_debut_livraison, heure_fin_livraison=l.heure_fin_livraison,
        temps_service_reel_s=l.temps_service_reel_s, temps_service_predit_s=l.temps_service_predit_s,
        client=ClientVue(
            id_client=c.id_client, nom=c.nom_client, adresse=c.adresse, code_postal=c.code_postal,
            ville=c.ville,
            latitude=float(c.latitude) if c.latitude is not None else None,
            longitude=float(c.longitude) if c.longitude is not None else None,
            creneau_debut=c.creneau_debut, creneau_fin=c.creneau_fin,
            type_camion_requis=c.type_camion_requis.value, etage=c.etage, ascenseur=c.ascenseur,
            telephone=c.telephone, digicode=c.digicode, notes_livraison=c.notes_livraison,
        ),
    )


def vue_tournee(db: Session, t: Tournee) -> TourneeVue:
    reglages = get_settings()
    zone = db.get(Zone, t.zone_id) if t.zone_id else None
    vehicule = db.get(Vehicule, t.vehicule_id) if t.vehicule_id else None
    depart_s, fin_s = service.bornes_horaires(t, reglages)
    return TourneeVue(
        id=t.id, date_tournee=t.date_tournee, statut=t.statut.value,
        zone=zone.nom if zone else None,
        vehicule=vehicule.immatriculation if vehicule else None,
        type_camion=vehicule.type_camion.value if vehicule else None,
        max_clients=t.max_clients, heure_depart=en_heure(depart_s), heure_fin=en_heure(fin_s),
        optimisee_le=t.optimisee_le,
        distance_totale_km=float(t.distance_totale_km) if t.distance_totale_km is not None else None,
        duree_estimee_min=t.duree_estimee_min,
        depot=Point(nom=reglages.depot_nom, latitude=reglages.depot_latitude,
                    longitude=reglages.depot_longitude),
        livraisons=[vue_livraison(l, c) for l, c in service.livraisons_de(db, t)],
    )


def _livraison_autorisee(request: Request, db: Session, livraison_id: int, u: Utilisateur) -> Livraison:
    livraison = service.livraison_du_livreur(db, livraison_id, u)
    if livraison is None:
        # Même réponse qu'une livraison inexistante ; la tentative est tracée
        journaliser(db, "ACCES_REFUSE", False, contexte_depuis(request), u.id, u.identifiant,
                    route=request.url.path, ressource="livraison", ressource_id=livraison_id)
        db.commit()
        raise HTTPException(404, "Livraison introuvable.")
    return livraison


@router.get("/tournees/du-jour", response_model=TourneeVue)
def tournee_du_jour(u: Utilisateur = Depends(livreur_connecte), db: Session = Depends(get_db)):
    """Liste des clients de la tournée du jour du livreur connecté (et de lui seul)."""
    try:
        return vue_tournee(db, service.tournee_du_jour(db, u, get_settings()))
    except service.ErreurTournee as e:
        raise _http(e) from None


@router.post("/tournees/du-jour/optimiser", response_model=ReponseOptimisation)
def optimiser(u: Utilisateur = Depends(livreur_connecte), db: Session = Depends(get_db)):
    """Bouton « Optimiser » : CVRPTW (créneaux, temps de service, véhicule, limite d'effectif).
    Avant le départ : toute la tournée. En cours de tournée : uniquement l'itinéraire restant."""
    reglages = get_settings()
    try:
        res = service.optimiser_tournee(db, service.tournee_du_jour(db, u, reglages), reglages)
    except service.ErreurTournee as e:
        raise _http(e) from None
    return ReponseOptimisation(**res.__dict__)


@router.post("/livraisons/{livraison_id}/demarrer", response_model=LivraisonVue)
def demarrer(livraison_id: int, request: Request, u: Utilisateur = Depends(livreur_connecte),
             db: Session = Depends(get_db)):
    """Bouton « Démarrer la livraison » : horodate l'arrivée chez le client."""
    livraison = _livraison_autorisee(request, db, livraison_id, u)
    try:
        livraison = service.demarrer_livraison(db, livraison, get_settings())
    except service.ErreurTournee as e:
        raise _http(e) from None
    return vue_livraison(livraison, db.get(Client, livraison.client_id))


@router.post("/livraisons/{livraison_id}/terminer", response_model=LivraisonVue)
def terminer(livraison_id: int, request: Request, demande: DemandeFin | None = None,
             u: Utilisateur = Depends(livreur_connecte), db: Session = Depends(get_db)):
    """Bouton « Terminer la livraison » : horodate le départ ; le temps réel est calculé en base."""
    livraison = _livraison_autorisee(request, db, livraison_id, u)
    try:
        issue = StatutLivraison(demande.issue if demande else "livre")
        livraison = service.terminer_livraison(db, livraison, issue, get_settings())
    except service.ErreurTournee as e:
        raise _http(e) from None
    return vue_livraison(livraison, db.get(Client, livraison.client_id))


@router.post("/tournees/du-jour/clients", response_model=ReponseAjout, status_code=201)
def ajouter_client(demande: DemandeAjoutClient, request: Request,
                   u: Utilisateur = Depends(livreur_connecte), db: Session = Depends(get_db),
                   geo: Geocodeur = Depends(geocodeur)):
    """Ajout d'une adresse à la volée. Refusé si le véhicule est inadapté ou si la limite
    d'effectif de la zone est atteinte ; appuyer ensuite sur « Optimiser » pour l'intégrer."""
    reglages = get_settings()
    try:
        tournee = service.tournee_du_jour(db, u, reglages)
        livraison, client, avertissement = service.ajouter_client(
            db, tournee, service.NouveauClient(**demande.model_dump()), geo, reglages, u,
            contexte_depuis(request))
    except service.ErreurTournee as e:
        raise _http(e) from None
    return ReponseAjout(livraison=vue_livraison(livraison, client), avertissement=avertissement)
