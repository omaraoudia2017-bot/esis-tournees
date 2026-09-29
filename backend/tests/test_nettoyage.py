"""Tests du nettoyage des lignes CSV (lancer : docker compose exec api python -m pytest -q)."""
from datetime import time

import pytest

from app.services.nettoyage import (LigneInvalide, decouper_adresse, extraire_acces,
                                    extraire_telephone, nettoyer_digicode, nettoyer_ligne,
                                    normaliser_ville, parser_creneau)

LIGNE = {
    "id_client": "0806756905", "nom_client": "MME. TEST", "telephone": "", "digicode": "0629 / 3263",
    "adresse": "31 RUE PIERRE DELORE BAT C, 69008 LYON",
    "creneau_horaire_prefere": "11H00-12H00", "historique_statut": "",
    "notes_livraison": "IMMEUBLE ETAGE: 2 SANS ASC.",
}


@pytest.mark.parametrize("adresse, attendu", [
    ("25 RUE DU BRULET, 69110 STE-FOY-LES-LYON", ("25 RUE DU BRULET", "69110", "SAINTE FOY LES LYON")),
    ("34 RUE GARIBALDI 1ER ETAGE, 69006 LYON 6E ARRONDISSEMENT", ("34 RUE GARIBALDI", "69006", "LYON")),
    ("4 AVENUE GALLINE 4 AVENUE GALLINE, 69100 VILLEURBANNE", ("4 AVENUE GALLINE", "69100", "VILLEURBANNE")),
    ("44 RUE DENUZIERE 69002, 69002 LYON", ("44 RUE DENUZIERE", "69002", "LYON")),
    ("ALLEE 9 5ETAGE CODE 3842A 184RUE MARCEL MERIEUX, 69007 LYON",
     ("184 RUE MARCEL MERIEUX", "69007", "LYON")),
    ("20 10EME RUE, 69800 SAINT-PRIEST", ("20 10EME RUE", "69800", "SAINT PRIEST")),
])
def test_decouper_adresse(adresse, attendu):
    assert decouper_adresse(adresse)[:3] == attendu


def test_code_acces_jamais_dans_l_adresse():
    voie, _, _, complement = decouper_adresse("7 RUE DES VERRIERS 9271 CODE PORTE PUIS 2E ETA, 69007 LYON")
    assert voie == "7 RUE DES VERRIERS"
    assert "9271" in complement           # conservé, mais dans les notes chiffrées


def test_adresse_sans_code_postal_rejetee():
    with pytest.raises(LigneInvalide):
        decouper_adresse("12 RUE SANS CODE POSTAL, LYON")


def test_ville_normalisee():
    assert normaliser_ville("TASSIN") == "TASSIN LA DEMI LUNE"
    assert normaliser_ville("CALUIRE-ET-CUIRE") == normaliser_ville("CALUIRE ET CUIRE")


@pytest.mark.parametrize("texte, attendu", [
    ("08H00-10H00", (time(8), time(10))),
    ("21H00-22H00", (time(21), time(22))),
])
def test_parser_creneau(texte, attendu):
    assert parser_creneau(texte) == attendu


@pytest.mark.parametrize("texte", ["10H00-08H00", "25H00-26H00", "matin", ""])
def test_creneau_invalide(texte):
    with pytest.raises(LigneInvalide):
        parser_creneau(texte)


@pytest.mark.parametrize("notes, attendu", [
    ("IMMEUBLE ETAGE: 3 SANS ASC. Sans ascenseur", (True, 3, False)),
    ("IMMEUBLE ETAGE: 4 AVEC ASC. BAT:B6", (True, 4, True)),
    ("IMMEUBLE ETAGE: 0. Appeler en arrivant", (True, 0, None)),
    ("Portail vert", (False, None, None)),
    (None, (None, None, None)),
])
def test_extraire_acces(notes, attendu):
    assert extraire_acces(notes) == attendu


def test_telephone_extrait_des_notes():
    assert extraire_telephone("", "appeler au 06.84.61.26.33 merci") == "0684612633"
    assert extraire_telephone(None, "code 0852") is None


def test_digicode():
    assert nettoyer_digicode("Non") is None
    assert nettoyer_digicode(" B4297 / B0565 ") == "B4297 / B0565"


def test_ligne_complete():
    c = nettoyer_ligne(LIGNE)
    assert c.adresse == "31 RUE PIERRE DELORE, 69008 LYON"
    assert c.notes_livraison.startswith("Complement d adresse : BAT C.")
    assert (c.est_immeuble, c.etage, c.ascenseur) == (True, 2, False)
    assert c.digicode == "0629 / 3263"


@pytest.mark.parametrize("champ, valeur", [("id_client", "12345"), ("adresse", ""), ("nom_client", " ")])
def test_ligne_invalide(champ, valeur):
    with pytest.raises(LigneInvalide):
        nettoyer_ligne({**LIGNE, champ: valeur})
