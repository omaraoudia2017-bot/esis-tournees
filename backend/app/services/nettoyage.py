"""Nettoyage d'une ligne du CSV d'échantillon (format tournees_esis.csv).

Fonctions pures (aucune dépendance à la base) : elles sont testées dans tests/test_nettoyage.py
et réutilisées par l'import administrateur.
"""
import re
from dataclasses import dataclass, field
from datetime import time

COLONNES_OBLIGATOIRES = ["id_client", "nom_client", "adresse", "creneau_horaire_prefere"]
COLONNES_ATTENDUES = COLONNES_OBLIGATOIRES + [
    "telephone", "digicode", "historique_statut", "notes_livraison",
]


class LigneInvalide(ValueError):
    """Ligne rejetée à l'import (le message explique pourquoi)."""


# --------------------------------------------------------------------------- adresse
RE_ADRESSE = re.compile(r"^(?P<voie>.*?),?\s*(?P<cp>\d{5})\s+(?P<ville>[^,]+)$")
RE_ARRONDISSEMENT = re.compile(r"\s+\d+\s*(E|ER|EME)\s+ARRONDISSEMENT$")

# Fragments qui ne font pas partie de la voie : ils partent dans les notes (chiffrées)
MOTIFS_COMPLEMENT = [
    # Codes d'accès glissés dans l'adresse : ne doivent JAMAIS rester en clair
    r"\b(?:[A-Z]?\d{3,6}[A-Z]?\s+)?CODE\b(?:\s+PORTE)?(?:\s+[A-Z]?\d{3,6}[A-Z]?)?(?:\s+PUIS\b.*$)?",
    r"\bINTERPHONE\s+\w+",
    r"\bPREMIER\s+ETAGE\b",
    r"\b\d+\s*(?:ER|EME|E)?\s*ETAGES?\b(?:\s+(?:DROITE|GAUCHE)\b)?",                # "1ER ETAGE", "2 EME ETAGES", "5ETAGE"
    r"\b(?:AVEC|SANS)\s+ASC\w*",
    r"\bMONTEE\s+A\s+ETAGE(?:\s+\d+)?\b",
    r"\bRDC\b",
    r"\bAPPARTEMENT\s+\w+",
    r"\bLGT\s+\w+",
    r"\bBAT(?:IMENT)?\.?\s+[A-Z]?\d*[A-Z]?\b",           # "BAT C", "BATIMENT A2", "BATIMENT 4"
    r"\bALLEE\s+(?:[A-Z]|\d+)\b(?!\s+(?:DE|DU|DES|D)\b)",  # entrée "ALLEE B", "ALLEE 9"
    r"\bRESIDENCE\b.*$",
    r"\bMAISON\b.*$",
    r"\bLE\s+(?:SAGITTAIRE|COTEAU|CHATEAU|VALLON)\b",        # noms de résidences observés
    r"\bLES\s+(?:CALADINES|FLORALIES|HAMEAUX)\b",
    r"\bLA\s+(?:FLORENTINE|TOUR\s+PART\s+DIEU)\b",
    r"\bZ\.?I\.?\s+\w+",
]
RE_COMPLEMENT = [re.compile(m) for m in MOTIFS_COMPLEMENT]


def _espaces(texte: str) -> str:
    return re.sub(r"\s+", " ", texte).strip(" ,.;-")


# Variantes observées dans l'échantillon -> nom de commune officiel
CORRECTIONS_VILLES = {
    "TASSIN": "TASSIN LA DEMI LUNE",
    "VILLEFRANCHE SUR SOANE": "VILLEFRANCHE SUR SAONE",
}


def normaliser_ville(ville: str) -> str:
    v = _espaces(ville.upper())
    v = RE_ARRONDISSEMENT.sub("", v)                    # "LYON 6E ARRONDISSEMENT" -> "LYON"
    v = re.sub(r"^STE[\s-]", "SAINTE ", v)
    v = re.sub(r"^ST[\s-]", "SAINT ", v)
    v = _espaces(v.replace("-", " "))                     # forme unique : "CALUIRE ET CUIRE"
    return CORRECTIONS_VILLES.get(v, v)


def nettoyer_voie(voie: str, code_postal: str) -> tuple[str, str | None]:
    """Retourne (voie exploitable pour le géocodage, complément retiré ou None)."""
    v = _espaces(voie.upper())
    v = re.sub(r"(\d)(?!I?EME\b)([A-Z]{3,})", r"\1 \2", v)            # "184RUE" -> "184 RUE", "5ETAGE" -> "5 ETAGE"
    v = re.sub(r"([A-Z]{3,})(\d{5})", r"\1 \2", v)         # "ETAGE69400" -> "ETAGE 69400"
    v = re.sub(rf"\b{code_postal}\b.*$", "", v)          # CP (et ville) répétés dans la voie
    complements = []
    for motif in RE_COMPLEMENT:
        m = motif.search(v)
        if m:
            complements.append(m.group(0).strip())
            v = v[: m.start()] + v[m.end():]
    v = _espaces(v)
    # Voie dupliquée : "4 AVENUE GALLINE 4 AVENUE GALLINE"
    mots = v.split()
    moitie = len(mots) // 2
    if len(mots) % 2 == 0 and moitie and mots[:moitie] == mots[moitie:]:
        v = " ".join(mots[:moitie])
    complement = _espaces(" ".join(complements)) or None
    return v, complement


def decouper_adresse(adresse: str) -> tuple[str, str, str, str | None]:
    """'25 RUE DU BRULET, 69110 STE-FOY-LES-LYON' -> (voie, cp, ville, complément)."""
    m = RE_ADRESSE.match(_espaces(adresse))
    if not m:
        raise LigneInvalide(f"adresse sans code postal exploitable : {adresse!r}")
    cp = m.group("cp")
    voie, complement = nettoyer_voie(m.group("voie"), cp)
    if not voie:
        raise LigneInvalide(f"voie vide après nettoyage : {adresse!r}")
    return voie, cp, normaliser_ville(m.group("ville")), complement


# --------------------------------------------------------------------------- créneau
RE_CRENEAU = re.compile(r"^(\d{1,2})H(\d{2})\s*-\s*(\d{1,2})H(\d{2})$")


def parser_creneau(texte: str) -> tuple[time, time]:
    """'08H00-10H00' -> (08:00, 10:00)."""
    m = RE_CRENEAU.match(texte.strip().upper())
    if not m:
        raise LigneInvalide(f"créneau illisible : {texte!r}")
    h1, m1, h2, m2 = map(int, m.groups())
    try:
        debut, fin = time(h1, m1), time(h2, m2)
    except ValueError:
        raise LigneInvalide(f"créneau hors bornes : {texte!r}") from None
    if fin <= debut:
        raise LigneInvalide(f"créneau incohérent (fin <= début) : {texte!r}")
    return debut, fin


# --------------------------------------------------------------------------- notes
RE_IMMEUBLE = re.compile(r"IMMEUBLE\s+ETAGE\s*:\s*(-?\d+)\.?\s*(AVEC|SANS)?\s*ASC", re.I)
RE_IMMEUBLE_SEUL = re.compile(r"IMMEUBLE\s+ETAGE\s*:\s*(-?\d+)", re.I)
RE_TELEPHONE = re.compile(r"(?<!\d)(0[1-9](?:[ .]?\d{2}){4})(?!\d)")


def extraire_acces(notes: str | None) -> tuple[bool | None, int | None, bool | None]:
    """Lit l'en-tête structuré 'IMMEUBLE ETAGE: 3 SANS ASC.' -> (immeuble, étage, ascenseur).

    Seul cet en-tête (produit par le logiciel source) est lu ici ; l'analyse du texte libre
    (QUAI, DIGICODE, impasse...) est faite plus tard pour le modèle prédictif.
    """
    if not notes:
        return None, None, None
    m = RE_IMMEUBLE.search(notes)
    if m:
        asc = {"AVEC": True, "SANS": False}.get((m.group(2) or "").upper())
        return True, int(m.group(1)), asc
    m = RE_IMMEUBLE_SEUL.search(notes)
    if m:
        return True, int(m.group(1)), None
    return False, None, None


def extraire_telephone(*textes: str | None) -> str | None:
    for t in textes:
        if t:
            m = RE_TELEPHONE.search(t)
            if m:
                return re.sub(r"[ .]", "", m.group(1))
    return None


# --------------------------------------------------------------------------- divers
MOTS_PROFESSIONNELS = re.compile(
    r"^(SOC\.|SOCIETE|CRECHE|EAJE|AGDS|POINT DE VENTE)|\b(SARL|SAS|SASU|EURL|LABORATOIRE|"
    r"FINANCE|LEARNING|RACCORDS|FRANCE|ENTREPRISE)\b"
)


def est_professionnel(nom: str, notes: str | None) -> bool:
    return bool(MOTS_PROFESSIONNELS.search(nom.upper())) or "ENTREPRISE" in (notes or "").upper()


def nettoyer_digicode(texte: str | None) -> str | None:
    t = _espaces(texte or "")
    return None if t.upper() in {"", "NON", "AUCUN", "-"} else t


@dataclass
class ClientNettoye:
    id_client: str
    nom_client: str
    adresse: str
    voie: str
    code_postal: str
    ville: str
    creneau_debut: time
    creneau_fin: time
    professionnel: bool
    telephone: str | None = None
    digicode: str | None = None
    notes_livraison: str | None = None
    est_immeuble: bool | None = None
    etage: int | None = None
    ascenseur: bool | None = None
    avertissements: list[str] = field(default_factory=list)


def nettoyer_ligne(ligne: dict[str, str]) -> ClientNettoye:
    """Transforme une ligne brute du CSV en données prêtes pour la table clients."""
    for col in COLONNES_OBLIGATOIRES:
        if not (ligne.get(col) or "").strip():
            raise LigneInvalide(f"colonne obligatoire vide : {col}")

    id_client = ligne["id_client"].strip()
    if not re.fullmatch(r"\d{10}", id_client):
        raise LigneInvalide(f"id_client invalide : {id_client!r}")

    nom = _espaces(ligne["nom_client"].upper())[:100]
    voie, cp, ville, complement = decouper_adresse(ligne["adresse"])
    debut, fin = parser_creneau(ligne["creneau_horaire_prefere"])

    notes = _espaces(ligne.get("notes_livraison") or "") or None
    avertissements = []
    if complement:
        # Rien n'est perdu : le complément (étage, bâtiment, code...) rejoint les notes chiffrées
        notes = f"Complement d adresse : {complement}. {notes or ''}".strip()
        avertissements.append(f"complément déplacé dans les notes : {complement}")

    immeuble, etage, asc = extraire_acces(notes)
    if etage is not None and not -5 <= etage <= 60:
        avertissements.append(f"étage improbable ignoré : {etage}")
        etage = None

    return ClientNettoye(
        id_client=id_client,
        nom_client=nom,
        adresse=f"{voie}, {cp} {ville}",
        voie=voie,
        code_postal=cp,
        ville=ville,
        creneau_debut=debut,
        creneau_fin=fin,
        professionnel=est_professionnel(nom, notes),
        telephone=extraire_telephone(ligne.get("telephone"), notes),
        digicode=nettoyer_digicode(ligne.get("digicode")),
        notes_livraison=notes,
        est_immeuble=immeuble,
        etage=etage,
        ascenseur=asc,
        avertissements=avertissements,
    )
