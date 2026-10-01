"""Variables du modèle prédictif : extraction Regex des notes, localisation, accès, créneau.

Les notes ci-dessous imitent les formats de l'échantillon réel, sans aucune donnée réelle.
"""
from datetime import time
from types import SimpleNamespace

from app.ml.variables import (COLONNES, extraire_mentions, nombre_de_mots, normaliser,
                              variables_client)

DEPOT = (45.704747, 4.766921)


def client(**champs):
    base = dict(type_camion_requis="normal", type_client="particulier", code_postal="69003",
                latitude=45.7600, longitude=4.8500, est_immeuble=None, etage=None, ascenseur=None,
                digicode=None, telephone=None, creneau_debut=time(8), creneau_fin=time(10),
                notes_livraison=None)
    base.update(champs)
    return SimpleNamespace(**base)


def actives(notes):
    return {nom for nom, oui in extraire_mentions(notes).items() if oui}


def test_normalisation_accents_et_majuscules():
    assert normaliser("Téléphoner à l'accueil, bâtiment B") == "TELEPHONER A L'ACCUEIL, BATIMENT B"
    assert normaliser(None) == ""


def test_mentions_formats_reels():
    assert actives("IMMEUBLE ETAGE: 1 SANS ASC. BAT: a gauche sonner sur le nom a l interphone") == {
        "mention_sans_ascenseur", "mention_batiment", "mention_interphone"}
    assert actives("Livraison sur palette par le QUAI.") == {"mention_quai"}
    assert actives("Code portail 1234B, maison au fond de l impasse") == {
        "mention_digicode", "mention_portail", "mention_acces_difficile"}
    assert actives("Digicode a l entree de la residence.") == {"mention_digicode", "mention_batiment"}
    assert actives("Appeler en arrivant") == {"mention_appel"}
    assert actives("Téléphoner avant de passer") == {"mention_appel"}
    assert actives("Entreprise. S adresser a l accueil.") == {"mention_accueil"}
    assert actives("Personne agee, patienter a l interphone.") == {"mention_patienter", "mention_interphone"}
    assert actives("Pas d'ascenseur, 3e etage") == {"mention_sans_ascenseur"}


def test_pas_de_faux_positifs():
    # mots qui contiennent les motifs sans être les mots recherchés
    assert actives("Hotel de ville, batterie, codex, quaiche, appelation, telle quelle") == set()
    assert actives(None) == set() and actives("") == set()


def test_nombre_de_mots():
    assert nombre_de_mots("Sonner 2 fois, porte B") == 5
    assert nombre_de_mots(None) == 0


def test_variables_acces_depuis_l_en_tete():
    v = variables_client(client(notes_livraison="IMMEUBLE ETAGE: 4 SANS ASC. Sonner fort"))
    assert v["immeuble"] and v["etage"] == 4 and v["ascenseur"] == "non"
    assert v["etages_a_pied"] == 4 and v["etage_connu"]
    v = variables_client(client(est_immeuble=True, etage=6, ascenseur=True))
    assert v["etages_a_pied"] == 0 and v["ascenseur"] == "oui"
    v = variables_client(client())
    assert not v["immeuble"] and v["etage"] == 0 and not v["etage_connu"]


def test_variables_localisation_et_type():
    v = variables_client(client(type_camion_requis="hayon", type_client="professionnel"), DEPOT)
    assert v["camion_hayon"] and v["professionnel"]
    assert v["code_postal"] == "69003" and v["departement"] == "69"
    assert v["zone"] == "Lyon - Villeurbanne"
    assert 1 < v["distance_centre_km"] < 3 and 6 < v["distance_depot_km"] < 10
    v = variables_client(client(latitude=None, longitude=None))
    assert not v["localise"] and v["zone"] == "inconnue" and v["distance_centre_km"] == 0.0


def test_variables_creneau():
    v = variables_client(client(creneau_debut=time(8), creneau_fin=time(10, 30)))
    assert v["largeur_creneau_min"] == 150 and v["heure_milieu_creneau"] == 9.25
    v = variables_client(client(creneau_debut=None, creneau_fin=None))
    assert not v["creneau_connu"] and v["largeur_creneau_min"] == 15 * 60


def test_jeu_de_colonnes_complet_et_sans_texte_libre():
    v = variables_client(client(notes_livraison="Digicode 4521, appeler Mme X au 06 00 00 00 00"),
                         DEPOT)
    assert set(COLONNES) == set(v)
    # aucune variable ne contient le texte des notes (pas de donnée personnelle)
    assert not any(isinstance(x, str) and ("06" in x or "MME" in x.upper()) for x in v.values())
