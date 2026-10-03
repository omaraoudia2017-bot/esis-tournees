"""Adresse du domicile du livreur interne : départ du matin et retour du soir avec le camion.

Règles (demande de l'exploitation) :
  - livreur PRESTATAIRE : départ et retour au dépôt (il rend le camion à l'entrepôt) ;
  - livreur INTERNE : domicile -> dépôt (chargement) -> clients -> domicile ; mais retour au DÉPÔT
    s'il reste des colis non livrés (client absent, refus, client reporté) à y déposer.

Protection de la vie privée (RGPD, minimisation) :
  - l'adresse est saisie par le livreur lui-même, pas par l'administrateur ;
  - adresse et position sont chiffrées en base (Fernet) ; la position est arrondie à 4 décimales
    (environ 10 m), largement suffisant pour estimer un trajet ;
  - géocodage SANS le cache partagé : l'adresse d'un salarié ne doit pas rester en clair
    dans la table cache_geocodage ;
  - l'administrateur sait seulement si l'adresse est renseignée, jamais laquelle ; le suivi de
    tournée côté administrateur n'affiche pas ce point sur la carte ;
  - passer un livreur en « prestataire » efface son adresse.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Utilisateur
from app.models.enums import PrecisionGeocodage, RoleUtilisateur, TypeLivreur
from app.optim.matrice import Point
from app.services.distances import haversine_km
from app.services.geocodage import GeocoderServiceError
from app.services.journal import ContexteRequete, journaliser
from app.services.nettoyage import nettoyer_voie, normaliser_ville


class ErreurDomicile(Exception):
    def __init__(self, statut: int, message: str):
        super().__init__(message)
        self.statut, self.message = statut, message


def est_interne(u: Utilisateur) -> bool:
    return u.role == RoleUtilisateur.LIVREUR and u.type_livreur == TypeLivreur.INTERNE


def position_domicile(u: Utilisateur | None) -> Point | None:
    """Point de départ / retour du livreur interne, None pour un prestataire ou sans adresse."""
    if u is None or not est_interne(u) or not u.domicile_position:
        return None
    lat, lon = (float(x) for x in u.domicile_position.split(";"))
    return Point(lat, lon)


def trajet_domicile_depot(u: Utilisateur, reglages: Settings) -> tuple[float, int] | None:
    """(km, minutes) estimés du domicile au dépôt, même méthode que la matrice des trajets."""
    domicile = position_domicile(u)
    if domicile is None:
        return None
    km = haversine_km(domicile.latitude, domicile.longitude, reglages.depot_latitude,
                      reglages.depot_longitude) * reglages.coefficient_routier
    return round(km, 1), round(km / reglages.vitesse_moyenne_kmh * 60)


def enregistrer_domicile(db: Session, u: Utilisateur, voie: str, code_postal: str, ville: str,
                         geocodeur, ctx: ContexteRequete | None) -> tuple[Utilisateur, str | None]:
    if not est_interne(u):
        raise ErreurDomicile(409, "Votre compte est « prestataire » : votre tournée part et revient au "
                                  "dépôt. Adressez-vous au planificateur si ce n'est pas le cas.")
    voie, _ = nettoyer_voie(voie, code_postal)            # complément (bâtiment, étage) inutile ici
    ville = normaliser_ville(ville)
    try:
        res = geocodeur.geocoder(voie, code_postal, ville)        # pas de cache : donnée personnelle
    except GeocoderServiceError:
        raise ErreurDomicile(503, "Service de localisation indisponible : réessayez plus tard.") from None
    if res.precision == PrecisionGeocodage.ECHEC or res.latitude is None:
        raise ErreurDomicile(422, "Adresse introuvable : vérifiez le numéro, la rue et le code postal.")
    u.domicile_adresse = f"{voie}, {code_postal} {ville}"
    u.domicile_position = f"{res.latitude:.4f};{res.longitude:.4f}"
    u.domicile_maj_le = datetime.now(timezone.utc)
    # Le journal ne contient JAMAIS l'adresse : seulement le fait qu'elle a changé
    journaliser(db, "DOMICILE_MODIFIE", True, ctx, u.id, u.identifiant, precision=res.precision.value)
    db.commit()
    avertissement = None
    if res.precision == PrecisionGeocodage.COMMUNE:
        avertissement = ("Adresse localisée seulement au centre de la commune : les heures de départ "
                         "et de retour seront approximatives.")
    return u, avertissement


def effacer_domicile(u: Utilisateur) -> bool:
    """Efface l'adresse (l'appelant journalise et fait le commit). Retourne True si elle existait."""
    existait = bool(u.domicile_adresse or u.domicile_position)
    u.domicile_adresse = u.domicile_position = None
    u.domicile_maj_le = datetime.now(timezone.utc) if existait else u.domicile_maj_le
    return existait


def supprimer_domicile(db: Session, u: Utilisateur, ctx: ContexteRequete | None) -> None:
    if effacer_domicile(u):
        journaliser(db, "DOMICILE_SUPPRIME", True, ctx, u.id, u.identifiant)
    db.commit()
