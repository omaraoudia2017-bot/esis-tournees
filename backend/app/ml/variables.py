"""Variables (features) du modèle prédictif de temps de service (cahier des charges v6, module 5).

Trois familles, comme prévu au cahier des charges :
  1. le type de livraison : camion hayon (palette) ou normal (colis), client professionnel ;
  2. la localisation : code postal, zone de sectorisation, distances au centre et au dépôt ;
  3. les contraintes d'accès extraites du texte libre `notes_livraison` par expressions
     régulières (« SANS ASC », « QUAI », « DIGICODE », interphone, portail...).

Le texte des notes n'est jamais conservé dans les variables : seules des indications oui / non
et des nombres en sortent. Le jeu d'entraînement ne contient donc aucune donnée personnelle.

Fonctions pures (aucune base de données), testées dans tests/test_variables.py.
"""
import re
import unicodedata
from datetime import time
from typing import Any

from app.services.distances import LYON_CENTRE, haversine_km
from app.services.nettoyage import extraire_acces
from app.services.sectorisation import zone_pour

# --------------------------------------------------------------------------- mentions dans les notes
# Motifs appliqués à un texte en MAJUSCULES et SANS ACCENTS (voir normaliser).
# Vocabulaire choisi à partir des mots les plus fréquents de l'échantillon réel.
MOTIFS_MENTIONS: dict[str, str] = {
    "mention_sans_ascenseur": r"\bSANS\s+ASC(?:ENSEUR)?\b|\bPAS\s+D\s*'?\s*ASC(?:ENSEUR)?\b",
    "mention_quai": r"\bQUAI\b",
    "mention_digicode": r"\bDIGI\s*CODE\b|\bCODE\b",
    "mention_interphone": r"\bINTERPHONE\b|\bSONN(?:ER|EZ|ETTE)\b",
    "mention_portail": r"\bPORTAIL\b|\bPORTILLON\b|\bBARRIERE\b",
    "mention_batiment": r"\bBAT(?:IMENT)?\b|\bRESIDENCE\b",
    "mention_appel": r"\bAPPEL(?:ER|EZ)?\b|\bTELEPHON(?:E|ER|EZ)\b|\bTEL\s*[:.]|\bCONTACTER\b",
    "mention_acces_difficile": (r"\bIMPASSE\b|\bAU\s+FOND\b|\bSTATIONNEMENT\s+DIFFICILE\b"
                                r"|\bETROITE?S?\b|\bESCALIERS?\b"),
    "mention_accueil": r"\bACCUEIL\b|\bRECEPTION\b",
    "mention_patienter": r"\bPATIENTER\b|\bPERSONNE\s+AGEE\b",
}
RE_MENTIONS = {nom: re.compile(motif) for nom, motif in MOTIFS_MENTIONS.items()}
RE_MOT = re.compile(r"[A-Z0-9]+")

JOURNEE_DEFAUT = (time(7, 0), time(22, 0))     # créneau inconnu : toute la journée


def normaliser(texte: str | None) -> str:
    """'Téléphoner à l'accueil' -> 'TELEPHONER A L'ACCUEIL' (majuscules, sans accents)."""
    if not texte:
        return ""
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    return sans_accents.upper()


def extraire_mentions(notes: str | None) -> dict[str, bool]:
    texte = normaliser(notes)
    return {nom: bool(motif.search(texte)) for nom, motif in RE_MENTIONS.items()}


def nombre_de_mots(notes: str | None) -> int:
    return len(RE_MOT.findall(normaliser(notes)))


# --------------------------------------------------------------------------- variables d'un client
def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _valeur(v: Any) -> Any:
    """Enum -> sa valeur ('hayon'), sinon inchangé."""
    return getattr(v, "value", v)


def variables_client(client: Any, depot: tuple[float, float] | None = None) -> dict[str, Any]:
    """Variables d'un client (objet Client ou tout objet ayant les mêmes attributs).

    Les valeurs manquantes sont remplacées par une valeur neutre et signalées par une
    variable « ..._connu » : un modèle Random Forest n'accepte pas de case vide.
    """
    notes = getattr(client, "notes_livraison", None)
    immeuble, etage, ascenseur = client.est_immeuble, client.etage, client.ascenseur
    if immeuble is None and etage is None and ascenseur is None:
        immeuble, etage, ascenseur = extraire_acces(notes)      # client pas encore enrichi
    etage_connu = etage is not None
    etage = max(etage or 0, 0)                                  # sous-sol compté comme rez-de-chaussée

    debut = client.creneau_debut or JOURNEE_DEFAUT[0]
    fin = client.creneau_fin or JOURNEE_DEFAUT[1]
    lat = float(client.latitude) if client.latitude is not None else None
    lon = float(client.longitude) if client.longitude is not None else None
    localise = lat is not None and lon is not None
    code_postal = client.code_postal or ""

    variables: dict[str, Any] = {
        # 1. type de livraison
        "camion_hayon": _valeur(client.type_camion_requis) == "hayon",
        "professionnel": _valeur(client.type_client) == "professionnel",
        # 2. localisation
        "code_postal": code_postal,
        "departement": code_postal[:2],
        "zone": zone_pour(code_postal, lat, lon).nom if localise else "inconnue",
        "distance_centre_km": round(haversine_km(lat, lon, *LYON_CENTRE), 2) if localise else 0.0,
        "distance_depot_km": round(haversine_km(lat, lon, *depot), 2) if localise and depot else 0.0,
        "localise": localise,
        # 3. accès au logement (en-tête structuré des notes, déjà extrait à l'import)
        "immeuble": bool(immeuble),
        "etage": etage,
        "etage_connu": etage_connu,
        "ascenseur": {True: "oui", False: "non"}.get(ascenseur, "inconnu"),
        # étages à monter à pied : la variable d'accès la plus directement liée au temps de service
        "etages_a_pied": etage if ascenseur is False else 0,
        "digicode_renseigne": bool(getattr(client, "digicode", None)),
        "telephone_renseigne": bool(getattr(client, "telephone", None)),
        # créneau horaire
        "largeur_creneau_min": _minutes(fin) - _minutes(debut),
        "heure_milieu_creneau": round((_minutes(debut) + _minutes(fin)) / 120, 2),
        "creneau_connu": client.creneau_debut is not None and client.creneau_fin is not None,
        # texte libre
        "nb_mots_notes": nombre_de_mots(notes),
    }
    variables.update(extraire_mentions(notes))
    return variables


# Colonnes attendues par le modèle (étape suivante : encodage puis Random Forest)
COLONNES_CATEGORIELLES = ["code_postal", "departement", "zone", "ascenseur"]
COLONNES_NUMERIQUES = ["distance_centre_km", "distance_depot_km", "etage", "etages_a_pied",
                       "largeur_creneau_min", "heure_milieu_creneau", "nb_mots_notes"]
COLONNES_BOOLEENNES = ["camion_hayon", "professionnel", "localise", "immeuble", "etage_connu",
                       "digicode_renseigne", "telephone_renseigne", "creneau_connu",
                       *MOTIFS_MENTIONS]
COLONNES = COLONNES_CATEGORIELLES + COLONNES_NUMERIQUES + COLONNES_BOOLEENNES
