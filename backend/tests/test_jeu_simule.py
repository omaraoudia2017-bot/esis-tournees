"""Jeu simulé : les notes inventées déclenchent exactement les mentions prévues du modèle."""
import random

from app.ml.variables import extraire_mentions
from scripts.generer_jeu_simule import (ARBRES, CONSIGNES, CONSIGNES_NEUTRES, MENTIONS_PROS,
                                        notes_pour)


def actives(texte):
    return {nom for nom, oui in extraire_mentions(texte).items() if oui}


def test_chaque_phrase_declenche_sa_seule_mention():
    for nom, phrases in CONSIGNES.items():
        for phrase in phrases:
            texte = phrase.format(lettre="B", arbre=ARBRES[0])
            assert actives(texte) == {nom}, (nom, texte)
    for phrase in CONSIGNES_NEUTRES:
        assert actives(phrase) == set(), phrase


def test_notes_pour_respecte_les_regles():
    tout = {nom: 1.0 for nom in CONSIGNES}
    rng = random.Random(1)
    pro = notes_pour(rng, True, 3, False, pro=True, taux=tout)
    assert pro.startswith("IMMEUBLE ETAGE: 3 SANS ASC.")
    assert actives(pro) >= set(CONSIGNES) | {"mention_sans_ascenseur"}
    particulier = notes_pour(rng, False, None, None, pro=False, taux=tout)
    assert not actives(particulier) & MENTIONS_PROS          # quai / accueil : pros seulement
    rien = {nom: 0.0 for nom in CONSIGNES}
    assert actives(notes_pour(random.Random(2), False, None, None, False, rien)) == set()
