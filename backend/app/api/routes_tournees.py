"""Routes du livreur : tournée du jour, bouton « Optimiser », pointage Démarrer / Terminer,
annulation du dernier clic, ajout d'une adresse en cours de tournée, remarques sur une adresse."""
from datetime import date, timedelta
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import exiger_role
from app.core.config import get_settings
from app.db.session import get_db
from app.models import Client, Livraison, RemarqueClient, Tournee, Utilisateur, Vehicule, Zone
from app.models.enums import RoleUtilisateur, StatutLivraison
from app.optim.probleme import en_heure
from app.schemas.tournee import (AnnulationPossible, ClientVue, DemandeAjoutClient, DemandeDomicile,
                                 DemandeFin, DemandeRemarque, DomicileVue, LivraisonVue, Point,
                                 RemarqueVue, ReponseAjout, ReponseAnnulation, ReponseOptimisation,
                                 TourneeLivreurResume, TourneeVue)
from app.services import domicile as domicile_service
from app.services import remarques as remarques_service
from app.services import tournees as service
from app.services.geocodage import Geocodeur
from app.services.journal import contexte_depuis, journaliser, tracer_consultation

router = APIRouter(tags=["Tournée du livreur"])
livreur_connecte = exiger_role(RoleUtilisateur.LIVREUR)


@lru_cache
def geocodeur() -> Geocodeur:
    """Un seul géocodeur pour toute l'API : son limiteur garantit 1 requête/s vers Nominatim.
    Remplacé par un faux géocodeur dans les tests (aucun appel réseau)."""
    return Geocodeur(get_settings().nominatim_user_agent)


def _http(erreur: service.ErreurTournee) -> HTTPException:
    return HTTPException(erreur.statut, erreur.message)


def vue_remarque(r: RemarqueClient, lecteur_id: int | None) -> RemarqueVue:
    return RemarqueVue(id=r.id, texte=r.texte, cree_le=r.cree_le,
                       de_moi=lecteur_id is not None and r.auteur_id == lecteur_id)


def vue_livraison(l: Livraison, c: Client, remarques: list[RemarqueClient] | None = None,
                  lecteur_id: int | None = None) -> LivraisonVue:
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
            remarques=[vue_remarque(r, lecteur_id) for r in remarques or []],
        ),
    )


def vue_livraison_seule(db: Session, l: Livraison, c: Client, lecteur_id: int) -> LivraisonVue:
    remarques = remarques_service.remarques_par_client(db, [c.id_client], get_settings().remarques_affichees)
    return vue_livraison(l, c, remarques[c.id_client], lecteur_id)


def vue_tournee(db: Session, t: Tournee, lecteur_id: int | None = None) -> TourneeVue:
    """Vue commune au livreur et au suivi par l'administrateur (lecteur_id : compte connecté)."""
    reglages = get_settings()
    lignes = service.livraisons_de(db, t)
    remarques = remarques_service.remarques_par_client(db, [c.id_client for _, c in lignes],
                                                       reglages.remarques_affichees)
    annulation = None
    if lecteur_id == t.livreur_id and (dernier := service.pointage_annulable(db, t, reglages)):
        annulation = AnnulationPossible(
            action=dernier.action, livraison_id=dernier.livraison.id, client=dernier.client.nom_client,
            expire_le=dernier.instant + timedelta(minutes=reglages.delai_annulation_pointage_min))
    zone = db.get(Zone, t.zone_id) if t.zone_id else None
    vehicule = db.get(Vehicule, t.vehicule_id) if t.vehicule_id else None
    depart_s, fin_s = service.bornes_horaires(t, reglages)
    livreur = db.get(Utilisateur, t.livreur_id) if t.livreur_id else None     # None : tournée en attente
    retour, colis = service.retour_prevu(db, t, livreur) if livreur else ("depot", 0)
    trajet = domicile_service.trajet_domicile_depot(livreur, reglages) if livreur else None
    # Le domicile n'est montré qu'au livreur lui-même (le suivi administrateur ne l'affiche pas)
    point_domicile = domicile_service.position_domicile(livreur) if lecteur_id == t.livreur_id else None
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
        livraisons=[vue_livraison(l, c, remarques[c.id_client], lecteur_id) for l, c in lignes],
        annulation_possible=annulation,
        type_livreur=livreur.type_livreur.value if livreur and livreur.type_livreur else None,
        retour=retour, colis_a_rapporter=colis,
        domicile=Point(nom="Domicile", latitude=point_domicile.latitude, longitude=point_domicile.longitude)
        if point_domicile else None,
        trajet_domicile_depot_min=trajet[1] if trajet else None,
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
def tournee_du_jour(request: Request, u: Utilisateur = Depends(livreur_connecte),
                    db: Session = Depends(get_db)):
    """Liste des clients de la tournée du jour du livreur connecté (et de lui seul).
    L'accès aux digicodes et téléphones est tracé au journal (au plus une fois par heure)."""
    try:
        tournee = service.tournee_du_jour(db, u, get_settings())
    except service.ErreurTournee as e:
        raise _http(e) from None
    tracer_consultation(db, u, contexte_depuis(request), tournee.id)
    return vue_tournee(db, tournee, lecteur_id=u.id)


@router.get("/tournees/mes-tournees", response_model=list[TourneeLivreurResume])
def mes_tournees(du: date | None = None, au: date | None = None, u: Utilisateur = Depends(livreur_connecte),
                 db: Session = Depends(get_db)):
    """Calendrier du livreur : ses tournées passées et à venir (par défaut 60 jours avant,
    30 jours après aujourd'hui ; un an au plus)."""
    aujourdhui = service.aujourdhui(get_settings())
    du, au = du or aujourdhui - timedelta(days=60), au or aujourdhui + timedelta(days=30)
    if au < du or (au - du).days > 366:
        raise HTTPException(422, "Période invalide (un an au plus).")
    return [TourneeLivreurResume(
        id=t.id, date_tournee=t.date_tournee, statut=t.statut.value, zone=zone, heure_depart=t.heure_depart,
        heure_fin=t.heure_fin, nb_clients=nb or 0, nb_livres=livres or 0, nb_non_livres=non_livres or 0,
        distance_totale_km=float(t.distance_totale_km) if t.distance_totale_km is not None else None,
        optimisee=t.optimisee_le is not None)
        for t, zone, nb, livres, non_livres in service.tournees_du_livreur(db, u, du, au)]


@router.get("/tournees/date/{jour}", response_model=TourneeVue)
def tournee_a_la_date(jour: date, request: Request, u: Utilisateur = Depends(livreur_connecte),
                      db: Session = Depends(get_db)):
    """Tournée du livreur connecté à une date donnée. Aujourd'hui : vue complète (comme
    /tournees/du-jour). Autre jour : consultation seule, sans digicode, téléphone ni notes."""
    tournee = service.tournee_du_livreur_le(db, u, jour)
    if tournee is None:
        raise HTTPException(404, f"Aucune tournée le {jour:%d/%m/%Y}.")
    if jour == service.aujourdhui(get_settings()):
        tracer_consultation(db, u, contexte_depuis(request), tournee.id)
        return vue_tournee(db, tournee, lecteur_id=u.id)
    vue = vue_tournee(db, tournee, lecteur_id=u.id)
    for l in vue.livraisons:
        l.client.telephone = l.client.digicode = l.client.notes_livraison = None
    vue.annulation_possible, vue.consultation_seule = None, True
    return vue


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
    return vue_livraison_seule(db, livraison, db.get(Client, livraison.client_id), u.id)


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
    return vue_livraison_seule(db, livraison, db.get(Client, livraison.client_id), u.id)


@router.post("/livraisons/{livraison_id}/annuler-demarrage", response_model=LivraisonVue)
def annuler_demarrage(livraison_id: int, request: Request, u: Utilisateur = Depends(livreur_connecte),
                      db: Session = Depends(get_db)):
    """Bouton « Annuler le démarrage » : client démarré par erreur, la livraison redevient
    « à livrer » tant qu'elle n'est pas terminée (sans délai). Tracé au journal."""
    livraison = _livraison_autorisee(request, db, livraison_id, u)
    try:
        livraison = service.annuler_demarrage(db, livraison, get_settings(), u, contexte_depuis(request))
    except service.ErreurTournee as e:
        raise _http(e) from None
    return vue_livraison_seule(db, livraison, db.get(Client, livraison.client_id), u.id)


@router.post("/tournees/du-jour/annuler-dernier-pointage", response_model=ReponseAnnulation)
def annuler_dernier_pointage(request: Request, u: Utilisateur = Depends(livreur_connecte),
                             db: Session = Depends(get_db)):
    """Clic fait par erreur : annule le DERNIER Démarrer ou Terminer de la tournée du jour,
    dans le délai prévu (10 min par défaut). Aucun identifiant n'est accepté : le livreur ne
    peut agir que sur sa propre tournée. L'annulation est tracée au journal de sécurité."""
    reglages = get_settings()
    try:
        tournee = service.tournee_du_jour(db, u, reglages)
        dernier = service.annuler_dernier_pointage(db, tournee, reglages, u, contexte_depuis(request))
    except service.ErreurTournee as e:
        raise _http(e) from None
    nom = dernier.client.nom_client
    message = (f"{nom} : fin de livraison annulée, la livraison est de nouveau en cours."
               if dernier.action == "terminer" else f"{nom} : démarrage annulé, la livraison est de nouveau à faire.")
    return ReponseAnnulation(action_annulee=dernier.action, message=message,
                             livraison=vue_livraison_seule(db, dernier.livraison, dernier.client, u.id))


@router.post("/livraisons/{livraison_id}/remarques", response_model=RemarqueVue, status_code=201)
def ajouter_remarque(livraison_id: int, demande: DemandeRemarque, request: Request,
                     u: Utilisateur = Depends(livreur_connecte), db: Session = Depends(get_db)):
    """Remarque sur l'adresse d'un client de SA tournée, visible lors des prochaines livraisons."""
    livraison = _livraison_autorisee(request, db, livraison_id, u)
    try:
        service._tournee_modifiable(db, livraison, get_settings())     # tournée du jour uniquement
    except service.ErreurTournee as e:
        raise _http(e) from None
    try:
        remarque = remarques_service.ajouter_remarque(db, livraison.client_id, u, demande.texte,
                                                      contexte_depuis(request))
    except remarques_service.ErreurRemarque as e:
        raise HTTPException(e.statut, e.message) from None
    return vue_remarque(remarque, u.id)


@router.delete("/remarques/{remarque_id}", status_code=204)
def supprimer_remarque(remarque_id: int, request: Request, u: Utilisateur = Depends(livreur_connecte),
                       db: Session = Depends(get_db)):
    """Le livreur supprime une de SES remarques (erreur de saisie)."""
    try:
        remarques_service.supprimer_remarque(db, remarque_id, u, contexte_depuis(request))
    except remarques_service.ErreurRemarque as e:
        raise HTTPException(e.statut, e.message) from None
    return Response(status_code=204)


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
    return ReponseAjout(livraison=vue_livraison_seule(db, livraison, client, u.id),
                        avertissement=avertissement)


# --------------------------------------------------------------------------- domicile (livreur interne)
def _vue_domicile(u: Utilisateur, avertissement: str | None = None) -> DomicileVue:
    trajet = domicile_service.trajet_domicile_depot(u, get_settings())
    return DomicileVue(type_livreur=u.type_livreur.value if u.type_livreur else None,
                       renseigne=bool(u.domicile_position), adresse=u.domicile_adresse,
                       modifie_le=u.domicile_maj_le,
                       trajet_depot_km=trajet[0] if trajet else None,
                       trajet_depot_min=trajet[1] if trajet else None, avertissement=avertissement)


@router.get("/moi/domicile", response_model=DomicileVue)
def lire_domicile(u: Utilisateur = Depends(livreur_connecte)):
    """Adresse de départ et de retour du livreur interne (lui seul y a accès)."""
    return _vue_domicile(u)


@router.put("/moi/domicile", response_model=DomicileVue)
def enregistrer_domicile(demande: DemandeDomicile, request: Request,
                         u: Utilisateur = Depends(livreur_connecte), db: Session = Depends(get_db),
                         geo: Geocodeur = Depends(geocodeur)):
    """Livreur interne uniquement. Adresse chiffrée, géocodée sans cache, jamais journalisée."""
    try:
        u, avertissement = domicile_service.enregistrer_domicile(
            db, u, demande.voie, demande.code_postal, demande.ville, geo, contexte_depuis(request))
    except domicile_service.ErreurDomicile as e:
        raise HTTPException(e.statut, e.message) from None
    return _vue_domicile(u, avertissement)


@router.delete("/moi/domicile", response_model=DomicileVue)
def supprimer_domicile(request: Request, u: Utilisateur = Depends(livreur_connecte),
                       db: Session = Depends(get_db)):
    """Le livreur retire son adresse : sa tournée part et revient alors au dépôt."""
    domicile_service.supprimer_domicile(db, u, contexte_depuis(request))
    return _vue_domicile(u)
