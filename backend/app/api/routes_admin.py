"""Routes réservées aux administrateurs (module 4) : comptes, import CSV, tournées, clients,
RGPD, tableau de bord, journal de sécurité.

Le contrôle de rôle est appliqué à TOUT le routeur (un livreur reçoit 403 et sa tentative est
journalisée). La logique est dans app/services/admin.py et app/services/import_csv.py.
"""
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import exiger_role
from app.api.routes_tournees import geocodeur, vue_remarque, vue_tournee
from app.core.config import get_settings
from app.db.session import get_db
from app.models import Client, JournalSecurite, Tournee, Utilisateur, Vehicule, Zone
from app.models.enums import RoleUtilisateur, TypeCamion
from app.optim.faisabilite import charge_par_heure
from app.schemas.admin import (ApercuClient, ApercuTourneeVue, BilanGeocodageVue, DemandeApercuTournee, ClientExclu, ClientRepartition, ClientResume, CompteVue,
                               DemandeAjoutExistant, DemandeAnonymisation, DemandeApercu, DemandeCompte, DemandeCorrection,
                               DemandeGeocodage, DemandeImport, DemandeReattribution, DemandeRepartition,
                               DemandeTournee,
                               DemandeTypeLivreur, DemandeVehicule, GroupeRepartition, LivreurDisponible,
                               PropositionRepartition, CasePlanning, LivreurPlanning, PlanningVue,
                               ReponseCompte, ReponseImport, RejetVue, TableauDeBordVue,
                               TourneeResume, VehiculeVue)
from app.schemas.auth import EvenementJournal
from app.schemas.tournee import RemarqueVue, TourneeVue
from app.schemas.trajets import EtapePlanificateur, JourneePlanificateur
from app.services import admin as service
from app.services import import_csv
from app.services import remarques as remarques_service
from app.services import planning as planning_service
from app.services import repartition as repartition_service
from app.services import trajets as trajets_service
from app.services.geocodage import Geocodeur
from app.services.journal import contexte_depuis, tracer_consultation

admin_connecte = exiger_role(RoleUtilisateur.ADMIN)
router = APIRouter(prefix="/admin", tags=["Administration"], dependencies=[Depends(admin_connecte)])

MOTIF_ID_CLIENT = r"^[0-9]{10}$"


def acteur(request: Request, u: Utilisateur = Depends(admin_connecte)) -> service.Acteur:
    return service.Acteur(u, contexte_depuis(request))


def _http(e: service.ErreurAdmin) -> HTTPException:
    return HTTPException(e.statut, e.message)


def _compte(u: Utilisateur, v: Vehicule | None) -> CompteVue:
    return CompteVue(id=u.id, identifiant=u.identifiant, nom=u.nom, prenom=u.prenom, role=u.role.value,
                     actif=u.actif, totp_active=u.totp_active,
                     verrouille=bool(u.verrouille_jusqua and u.verrouille_jusqua > datetime.now(timezone.utc)),
                     vehicule_id=v.id if v else None, vehicule=v.immatriculation if v else None,
                     type_camion=v.type_camion.value if v else None,
                     derniere_connexion=u.derniere_connexion,
                     conditions_version=u.conditions_version,
                     conditions_acceptees_le=u.conditions_acceptees_le,
                     type_livreur=u.type_livreur.value if u.type_livreur else None,
                     domicile_renseigne=bool(u.domicile_position),
                     activation_en_attente=u.mdp_a_definir,
                     activation_expire_le=u.activation_expire_le if u.mdp_a_definir else None)


def _compte_de(db: Session, u: Utilisateur) -> CompteVue:
    return _compte(u, db.get(Vehicule, u.vehicule_id) if u.vehicule_id else None)


def _resume(c: Client, zone: str | None) -> ClientResume:
    return ClientResume(
        id_client=c.id_client, nom=c.nom_client, adresse=c.adresse, code_postal=c.code_postal,
        ville=c.ville, zone=zone, latitude=float(c.latitude) if c.latitude is not None else None,
        longitude=float(c.longitude) if c.longitude is not None else None,
        precision=c.geocode_precision.value if c.geocode_precision else None,
        type_camion_requis=c.type_camion_requis.value, creneau_debut=c.creneau_debut,
        creneau_fin=c.creneau_fin, source=c.source.value, actif=c.actif)


def _resume_de(db: Session, c: Client) -> ClientResume:
    zone = db.get(Zone, c.zone_id) if c.zone_id else None
    return _resume(c, zone.nom if zone else None)


# --------------------------------------------------------------------------- comptes
@router.get("/utilisateurs", response_model=list[CompteVue])
def lister_comptes(db: Session = Depends(get_db)):
    return [_compte(u, v) for u, v in service.lister_comptes(db)]


@router.post("/utilisateurs", response_model=ReponseCompte, status_code=201)
def creer_compte(demande: DemandeCompte, a: service.Acteur = Depends(acteur), db: Session = Depends(get_db)):
    """Un code d'activation est généré et renvoyé UNE fois : à la 1re connexion, le salarié choisit
    son mot de passe puis active la 2FA."""
    try:
        u, code = service.creer_compte(db, a, demande.identifiant, demande.nom, demande.prenom,
                                      RoleUtilisateur(demande.role), demande.vehicule_id,
                                      demande.type_livreur)
    except service.ErreurAdmin as e:
        raise _http(e) from None
    return ReponseCompte(compte=_compte_de(db, u), code_activation=code, code_expire_le=u.activation_expire_le)


@router.post("/utilisateurs/{utilisateur_id}/vehicule", response_model=CompteVue)
def affecter_vehicule(utilisateur_id: int, demande: DemandeVehicule,
                      a: service.Acteur = Depends(acteur), db: Session = Depends(get_db)):
    try:
        return _compte_de(db, service.affecter_vehicule(db, a, utilisateur_id, demande.vehicule_id))
    except service.ErreurAdmin as e:
        raise _http(e) from None


@router.post("/utilisateurs/{utilisateur_id}/type-livreur", response_model=CompteVue)
def changer_type_livreur(utilisateur_id: int, demande: DemandeTypeLivreur,
                         a: service.Acteur = Depends(acteur), db: Session = Depends(get_db)):
    """Interne <-> prestataire. Passer en prestataire efface l'adresse du domicile."""
    try:
        return _compte_de(db, service.changer_type_livreur(db, a, utilisateur_id, demande.type_livreur))
    except service.ErreurAdmin as e:
        raise _http(e) from None


@router.post("/utilisateurs/{utilisateur_id}/{action}", response_model=ReponseCompte)
def agir_sur_compte(utilisateur_id: int,
                    action: str = Path(pattern="^(" + "|".join(service.ACTIONS_COMPTE) + ")$"),
                    a: service.Acteur = Depends(acteur), db: Session = Depends(get_db)):
    """desactiver, reactiver, deverrouiller, reinitialiser-mdp (nouveau code d'activation renvoyé une
    fois), reinitialiser-2fa (nouvel enrôlement à la prochaine connexion)."""
    try:
        u, code = service.agir_sur_compte(db, a, utilisateur_id, action)
    except service.ErreurAdmin as e:
        raise _http(e) from None
    return ReponseCompte(compte=_compte_de(db, u), code_activation=code,
                         code_expire_le=u.activation_expire_le if code else None)


@router.get("/vehicules", response_model=list[VehiculeVue])
def lister_vehicules(db: Session = Depends(get_db)):
    return [VehiculeVue(id=v.id, immatriculation=v.immatriculation, type_camion=v.type_camion.value,
                        actif=v.actif, utilise_par=par) for v, par in service.lister_vehicules(db)]


# --------------------------------------------------------------------------- import CSV
@router.post("/import-csv", response_model=ReponseImport)
def importer(demande: DemandeImport, a: service.Acteur = Depends(acteur), db: Session = Depends(get_db),
             geo: Geocodeur = Depends(geocodeur)):
    """Import d'un fichier au format tournees_esis.csv : contrôle des champs obligatoires,
    nettoyage, création / mise à jour des clients, puis géocodage des nouvelles adresses
    (au plus `max_geocodages` ici ; le reste via /admin/clients/geocoder)."""
    try:
        lignes = import_csv.lire_texte(demande.contenu)
    except import_csv.ErreurImport as e:
        a.tracer(db, "IMPORT_CSV", False, fichier=demande.nom_fichier[:100], motif=str(e))
        db.commit()
        raise HTTPException(422, str(e)) from None
    bilan = import_csv.importer_lignes(db, lignes)
    a.tracer(db, "IMPORT_CSV", True, fichier=demande.nom_fichier[:100], lignes=bilan.lignes,
             crees=bilan.crees, mis_a_jour=bilan.mis_a_jour, rejetes=len(bilan.rejets))
    db.commit()                      # les clients sont enregistrés avant le géocodage (plus long)
    geocodage = import_csv.geocoder_en_attente(db, geo, demande.max_geocodages, bilan.ids_valides)
    db.commit()
    return ReponseImport(lignes=bilan.lignes, crees=bilan.crees, mis_a_jour=bilan.mis_a_jour,
                         a_regeocoder=bilan.a_regeocoder, avec_avertissement=bilan.avec_avertissement,
                         rejets=[RejetVue(**r.__dict__) for r in bilan.rejets],
                         ids_valides=bilan.ids_valides,
                         geocodage=BilanGeocodageVue(**geocodage.__dict__))


@router.post("/clients/geocoder", response_model=BilanGeocodageVue)
def geocoder(demande: DemandeGeocodage, a: service.Acteur = Depends(acteur),
             db: Session = Depends(get_db), geo: Geocodeur = Depends(geocodeur)):
    """Géocode un lot d'adresses en attente (Nominatim : 1 requête par seconde)."""
    bilan = import_csv.geocoder_en_attente(db, geo, demande.maximum)
    a.tracer(db, "ADMIN_GEOCODAGE", not bilan.service_indisponible, traites=bilan.traites,
             restants=bilan.restants)
    db.commit()
    return BilanGeocodageVue(**bilan.__dict__)


# --------------------------------------------------------------------------- tournées
def _resumes(lignes) -> list[TourneeResume]:
    return [TourneeResume(
        id=t.id, date_tournee=t.date_tournee, livreur=u.identifiant if u else None,
        livreur_nom=f"{u.prenom} {u.nom}" if u else None,
        vehicule=v.immatriculation if v else None, type_camion=v.type_camion.value if v else None,
        zone=z.nom if z else None, statut=t.statut.value, nb_clients=nb or 0, max_clients=t.max_clients,
        nb_terminees=faites or 0, nb_a_reporter=reportes or 0, heure_depart=t.heure_depart,
        heure_fin=t.heure_fin, optimisee=t.optimisee_le is not None,
        distance_totale_km=float(t.distance_totale_km) if t.distance_totale_km is not None else None,
        attribuee_le=t.cree_le,
    ) for t, u, v, z, nb, faites, reportes in lignes]


@router.get("/tournees", response_model=list[TourneeResume])
def lister_tournees(jour: date | None = Query(None, alias="date"), db: Session = Depends(get_db)):
    jour = jour or service.aujourdhui(get_settings())
    return _resumes(service.lister_tournees(db, jour))


@router.get("/planning", response_model=PlanningVue)
def planning(debut: date | None = None, jours: int = Query(7, ge=1, le=14), db: Session = Depends(get_db)):
    """Planning des livreurs actifs : une case par livreur et par jour (tournée, journée de trajets
    ou libre) + les tournées à attribuer. Par défaut : la semaine en cours (à partir du lundi)."""
    aujourdhui = service.aujourdhui(get_settings())
    debut = debut or aujourdhui - timedelta(days=aujourdhui.weekday())
    p = planning_service.planning(db, debut, jours)
    cases = [CasePlanning(
        livreur_id=c.tournee.livreur_id, date=c.tournee.date_tournee, nature="tournee", tournee_id=c.tournee.id,
        statut=c.tournee.statut.value, heure_depart=c.tournee.heure_depart, heure_fin=c.tournee.heure_fin,
        zone=c.zone, nb_clients=c.nb_clients, nb_terminees=c.nb_terminees,
        optimisee=c.tournee.optimisee_le is not None) for c in p.tournees]
    occupees = {(c.livreur_id, c.date) for c in cases}
    cases += [CasePlanning(livreur_id=j.livreur_id, date=j.date_journee, nature="trajets", nb_etapes=n,
                           heure_depart=j.heure_depart, optimisee=j.optimisee_le is not None)
              for j, n in p.trajets if (j.livreur_id, j.date_journee) not in occupees]
    attente = _resumes(service.lister_tournees(db, None, en_attente_depuis=aujourdhui))
    types = planning_service.types_camion_requis(db, [t.id for t in attente])
    for t in attente:
        t.type_camion_requis = types.get(t.id)
    return PlanningVue(
        debut=p.debut, fin=p.fin, jours=[p.debut + timedelta(days=k) for k in range(jours)],
        livreurs=[LivreurPlanning(id=u.id, identifiant=u.identifiant, nom=f"{u.prenom} {u.nom}",
                                  vehicule=v.immatriculation if v else None,
                                  type_camion=v.type_camion.value if v else None,
                                  type_livreur=u.type_livreur.value if u.type_livreur else None)
                  for u, v in p.livreurs],
        cases=cases, a_attribuer=attente)


@router.post("/tournees/apercu", response_model=ApercuTourneeVue)
def apercu_tournee(demande: DemandeApercuTournee, db: Session = Depends(get_db)):
    """Aperçu d'une tournée avant attribution (ordre horaire, codes postaux, charge par heure,
    départ du dépôt au plus tard). N'enregistre rien."""
    reglages = get_settings()
    a = service.apercu_tournee(db, reglages, demande.clients, demande.periode)
    return ApercuTourneeVue(
        zone=a.zone.nom if a.zone else None, limite=a.limite, debut_periode=_heure_s(a.periode.depart_s),
        fin_periode=_heure_s(a.periode.fin_s),
        clients=[ClientRepartition(id_client=c.id_client, nom=c.nom_client, code_postal=c.code_postal, ville=c.ville,
                                   creneau_debut=c.creneau_debut, creneau_fin=c.creneau_fin,
                                   type_camion_requis=c.type_camion_requis.value,
                                   heure_visee=a.heures.get(c.id_client)) for c in a.clients],
        charge_par_heure={str(h): n for h, n in charge_par_heure(list(a.heures.values())).items()},
        refuses=a.refuses, inconnus=a.inconnus, non_localises=a.non_localises,
        depart_au_plus_tard=_heure_s(a.depart_au_plus_tard_s) if a.depart_au_plus_tard_s is not None else None)


@router.get("/tournees/en-attente", response_model=list[TourneeResume])
def tournees_en_attente(db: Session = Depends(get_db)):
    """Tournées à venir dont l'attribution a été annulée : à redonner à un livreur."""
    return _resumes(service.lister_tournees(db, None, en_attente_depuis=service.aujourdhui(get_settings())))


@router.post("/tournees/{tournee_id}/desattribuer", response_model=TourneeResume)
def desattribuer_tournee(tournee_id: int, a: service.Acteur = Depends(acteur), db: Session = Depends(get_db)):
    """Annule l'attribution (tournée pas encore commencée) : la tournée passe « en attente »."""
    try:
        t = service.desattribuer_tournee(db, a, get_settings(), tournee_id)
    except service.ErreurAdmin as e:
        raise _http(e) from None
    return next(r for r in _resumes(service.lister_tournees(db, t.date_tournee)) if r.id == t.id)


@router.post("/tournees/{tournee_id}/attribuer", response_model=TourneeResume)
def reattribuer_tournee(tournee_id: int, demande: DemandeReattribution, a: service.Acteur = Depends(acteur),
                        db: Session = Depends(get_db)):
    """Donne la tournée (en attente, ou d'un autre livreur) à un livreur disponible ce jour-là."""
    try:
        t = service.reattribuer_tournee(db, a, get_settings(), tournee_id, demande.livreur_id)
    except service.ErreurAdmin as e:
        raise _http(e) from None
    return next(r for r in _resumes(service.lister_tournees(db, t.date_tournee)) if r.id == t.id)


@router.post("/tournees", response_model=TourneeResume, status_code=201)
def creer_tournee(demande: DemandeTournee, a: service.Acteur = Depends(acteur),
                  db: Session = Depends(get_db)):
    """Attribue une tournée à un livreur (clients dans l'ordre horaire de leurs créneaux : le livreur
    l'optimise avec son bouton). Refus : véhicule incompatible, limite d'effectif (zone et période),
    client déjà planifié ce jour-là, client sans position. Avertissements : créneaux surchargés
    (plus de N clients par heure), départ du dépôt nécessaire avant le début de la période."""
    try:
        t = service.creer_tournee(db, a, get_settings(), demande.livreur_id, demande.clients,
                                  demande.date_tournee, demande.periode, demande.remplacer)
    except service.ErreurAdmin as e:
        raise _http(e) from None
    resume = next(r for r in _resumes(service.lister_tournees(db, t.date_tournee)) if r.id == t.id)
    resume.avertissements = service.avertissements_tournee(db, get_settings(), t)
    return resume


@router.get("/tournees/{tournee_id}", response_model=TourneeVue)
def detail_tournee(tournee_id: int, a: service.Acteur = Depends(acteur), db: Session = Depends(get_db)):
    """Même vue que l'écran du livreur (carte, ordre, pointages), pour le suivi en direct.
    Elle contient les digicodes : la consultation est tracée (au plus une fois par heure)."""
    tournee = db.get(Tournee, tournee_id)
    if tournee is None:
        raise HTTPException(404, "Tournée introuvable.")
    tracer_consultation(db, a.utilisateur, a.ctx, tournee.id)
    return vue_tournee(db, tournee, lecteur_id=a.utilisateur.id)


@router.post("/tournees/{tournee_id}/clients", status_code=201)
def ajouter_client_existant(tournee_id: int, demande: DemandeAjoutExistant,
                            a: service.Acteur = Depends(acteur), db: Session = Depends(get_db)):
    try:
        livraison = service.ajouter_client_existant(db, a, get_settings(), tournee_id, demande.id_client)
    except service.ErreurAdmin as e:
        raise _http(e) from None
    return {"livraison_id": livraison.id, "message": "Client ajouté : le livreur doit relancer l'optimisation."}


# --------------------------------------------------------------------------- répartition en tournées
def _heure_s(secondes: int) -> time:
    secondes = min(max(0, secondes), 24 * 3600 - 60)
    return time(secondes // 3600, secondes % 3600 // 60)


@router.post("/repartition/apercu", response_model=list[ApercuClient])
def apercu_repartition(demande: DemandeApercu, db: Session = Depends(get_db)):
    """Code postal, ville et créneau des clients d'une liste (grille « code postal × heure »).
    Ni adresse, ni téléphone, ni digicode : le strict nécessaire (minimisation)."""
    ids = list(dict.fromkeys(i.strip() for i in demande.clients))
    clients = db.scalars(select(Client).where(Client.id_client.in_(ids), Client.actif.is_(True))
                         .order_by(Client.code_postal, Client.creneau_debut)).all()
    return [ApercuClient(id_client=c.id_client, nom=c.nom_client, code_postal=c.code_postal, ville=c.ville,
                         creneau_debut=c.creneau_debut, creneau_fin=c.creneau_fin,
                         type_camion_requis=c.type_camion_requis.value, localise=c.latitude is not None)
            for c in clients]


@router.post("/repartition", response_model=PropositionRepartition)
def proposer_repartition(demande: DemandeRepartition, db: Session = Depends(get_db)):
    """Classe une liste de clients (zone, plage horaire, type de camion), la coupe selon la limite
    d'effectif de chaque zone et propose un livreur disponible par groupe. N'enregistre rien :
    chaque groupe validé est créé avec POST /admin/tournees (mêmes contrôles)."""
    reglages = get_settings()
    jour = demande.date_tournee or service.aujourdhui(reglages)
    if jour < service.aujourdhui(reglages):
        raise HTTPException(422, "Impossible de planifier une tournée dans le passé.")
    prop = repartition_service.proposer(db, reglages, jour, demande.clients, demande.journee_continue,
                                        demande.clients_par_heure)
    return PropositionRepartition(
        date_tournee=jour,
        groupes=[GroupeRepartition(
            numero=k, zone=g.zone.nom if g.zone else None, periode=g.periode, type_camion=g.type_camion,
            limite=g.limite, codes_postaux=g.codes_postaux,
            clients=[ClientRepartition(id_client=c.id_client, nom=c.nom_client, code_postal=c.code_postal,
                                       ville=c.ville, creneau_debut=c.creneau_debut, creneau_fin=c.creneau_fin,
                                       type_camion_requis=c.type_camion_requis.value,
                                       heure_visee=g.heures.get(c.id_client)) for c in g.clients],
            livreur_propose_id=g.livreur.id if g.livreur else None,
            debut_periode=_heure_s(g.debut_periode_s),
            depart_au_plus_tard=_heure_s(g.depart_au_plus_tard_s) if g.depart_au_plus_tard_s is not None else None,
            charge_par_heure={str(h): n for h, n in charge_par_heure(list(g.heures.values())).items()})
            for k, g in enumerate(prop.groupes, start=1)],
        livreurs_disponibles=[LivreurDisponible(id=u.id, identifiant=u.identifiant, nom=f"{u.prenom} {u.nom}",
                                                vehicule=v.immatriculation, type_camion=v.type_camion.value)
                              for u, v in prop.livreurs],
        exclus=[ClientExclu(id_client=i, motif=m) for i, m in prop.exclus])


# --------------------------------------------------------------------------- journées de trajets (lecture seule)
@router.get("/trajets", response_model=list[JourneePlanificateur])
def journees_de_trajets(jour: date | None = Query(None, alias="date"), db: Session = Depends(get_db)):
    """Étapes saisies par les livreurs sans tournée. Lecture seule : le planificateur ne voit ni la
    position ni l'adresse du domicile, seulement « Dépôt » ou « Domicile »."""
    jour = jour or service.aujourdhui(get_settings())
    return [JourneePlanificateur(
        livreur=u.identifiant, livreur_nom=f"{u.prenom} {u.nom}", depart=j.depart, retour=j.retour,
        heure_depart=j.heure_depart, optimisee=j.optimisee_le is not None,
        distance_km=float(j.distance_km) if j.distance_km is not None else None, duree_min=j.duree_min,
        nb_faites=sum(e.faite_le is not None for e in etapes),
        etapes=[EtapePlanificateur(ordre=e.ordre, libelle=e.libelle, adresse=e.adresse, rdv_debut=e.rdv_debut,
                                   rdv_fin=e.rdv_fin, duree_min=e.duree_min, heure_prevue=e.heure_prevue,
                                   faite_le=e.faite_le) for e in etapes],
    ) for j, u, etapes in trajets_service.journees_du(db, jour)]


# --------------------------------------------------------------------------- clients et RGPD
@router.get("/clients", response_model=list[ClientResume])
def rechercher_clients(recherche: str | None = Query(None, max_length=100),
                       a_corriger: bool = False, limite: int = Query(50, ge=1, le=500),
                       db: Session = Depends(get_db)):
    """Recherche (identifiant, nom, adresse, code postal, ville). `a_corriger` : clients réels mal localisés
    (centre de la commune ou introuvables), à repositionner à la main."""
    return [_resume(c, zone) for c, zone in service.rechercher_clients(db, recherche, a_corriger, limite)]


@router.post("/clients/{id_client}/correction", response_model=ClientResume)
def corriger_client(demande: DemandeCorrection, id_client: str = Path(pattern=MOTIF_ID_CLIENT),
                    a: service.Acteur = Depends(acteur), db: Session = Depends(get_db)):
    """Position corrigée à la main (précision « manuelle ») et / ou type de camion requis."""
    try:
        client = service.corriger_client(
            db, a, id_client, demande.latitude, demande.longitude,
            TypeCamion(demande.type_camion_requis) if demande.type_camion_requis else None)
    except service.ErreurAdmin as e:
        raise _http(e) from None
    return _resume_de(db, client)


@router.get("/clients/{id_client}/export")
def exporter_client(id_client: str = Path(pattern=MOTIF_ID_CLIENT), a: service.Acteur = Depends(acteur),
                    db: Session = Depends(get_db)) -> dict:
    """Droit d'accès RGPD : toutes les données du client (déchiffrées). Action journalisée."""
    try:
        return service.exporter_client(db, a, get_settings(), id_client)
    except service.ErreurAdmin as e:
        raise _http(e) from None


@router.post("/clients/{id_client}/anonymiser", response_model=ClientResume)
def anonymiser_client(demande: DemandeAnonymisation, id_client: str = Path(pattern=MOTIF_ID_CLIENT),
                      a: service.Acteur = Depends(acteur), db: Session = Depends(get_db)):
    """Droit à l'effacement RGPD (irréversible) : l'identifiant doit être ressaisi."""
    if demande.confirmation != id_client:
        raise HTTPException(422, "Confirmation incorrecte : ressaisissez l'identifiant du client.")
    try:
        client = service.anonymiser_client(db, a, get_settings(), id_client)
    except service.ErreurAdmin as e:
        raise _http(e) from None
    return _resume_de(db, client)


# --------------------------------------------------------------------------- suivi
@router.get("/clients/{id_client}/remarques", response_model=list[RemarqueVue])
def remarques_client(id_client: str = Path(pattern=MOTIF_ID_CLIENT), db: Session = Depends(get_db)):
    """Toutes les remarques laissées sur l'adresse d'un client (contrôle du contenu)."""
    if db.get(Client, id_client) is None:
        raise HTTPException(404, "Client introuvable.")
    return [vue_remarque(r, None) for r in remarques_service.toutes_les_remarques(db, id_client)]


@router.delete("/remarques/{remarque_id}", status_code=204)
def supprimer_remarque(remarque_id: int, a: service.Acteur = Depends(acteur), db: Session = Depends(get_db)):
    """Suppression d'une remarque inadaptée (jugement sur la personne, donnée excessive)."""
    try:
        remarques_service.supprimer_remarque(db, remarque_id, a.utilisateur, a.ctx, admin=True)
    except remarques_service.ErreurRemarque as e:
        raise HTTPException(e.statut, e.message) from None
    return Response(status_code=204)


@router.get("/tableau-de-bord", response_model=TableauDeBordVue)
def tableau_de_bord(jours: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    """Respect des créneaux, remplissage des tournées, écart temps de service prédit / réel,
    alertes de sécurité, sur les `jours` derniers jours."""
    return TableauDeBordVue(**service.tableau_de_bord(db, get_settings(), jours).__dict__)


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
