"""Tests de la segmentation (hayon / normal, périodes) et du budget de calcul."""
from app.optim.probleme import ClientTournee, en_secondes
from app.optim.segmentation import Periode, dimensionner, periode_de, repartir_budget, segmenter

S = en_secondes
PERIODES = [Periode("matin", S("07:00"), S("14:00"), S("13:00")),
            Periode("apres-midi", S("13:00"), S("19:00"), S("18:00")),
            Periode("soiree", S("17:00"), S("22:00"), 24 * 3600)]
JOURNEE = [Periode("journee", S("07:00"), S("22:00"), 24 * 3600)]


def c(debut, fin, type_requis="normal"):
    return ClientTournee("x", S(debut), S(fin), 600, type_requis)


def test_rattachement_par_milieu_du_creneau():
    assert periode_de(c("08:00", "10:00"), PERIODES).nom == "matin"
    assert periode_de(c("11:00", "13:00"), PERIODES).nom == "matin"
    assert periode_de(c("12:00", "14:00"), PERIODES).nom == "apres-midi"
    assert periode_de(c("16:00", "18:00"), PERIODES).nom == "apres-midi"
    assert periode_de(c("17:00", "19:00"), PERIODES).nom == "soiree"
    assert periode_de(c("21:00", "22:00"), PERIODES).nom == "soiree"


def test_la_periode_contient_le_creneau():
    # Le créneau chevauche toujours largement sa période : on peut livrer à l'heure
    for debut, fin in [("07:00", "08:00"), ("12:00", "14:00"), ("16:00", "18:00"),
                       ("17:00", "19:00"), ("21:00", "22:00")]:
        p = periode_de(c(debut, fin), PERIODES)
        assert S(fin) - p.depart_s >= 30 * 60 and p.fin_s - S(debut) >= 60 * 60


def test_segments_type_et_periode():
    clients = [c("08:00", "09:00"), c("19:00", "20:00"), c("08:00", "10:00", "hayon"),
               c("09:00", "10:00"), c("15:00", "16:00")]
    segs = [(s.type_camion, s.periode.nom, s.indices) for s in segmenter(clients, PERIODES)]
    assert segs == [("hayon", "matin", [2]), ("normal", "matin", [0, 3]),
                    ("normal", "apres-midi", [4]), ("normal", "soiree", [1])]


def test_journee_continue_seul_le_type_compte():
    clients = [c("08:00", "09:00"), c("19:00", "20:00"), c("08:00", "10:00", "hayon")]
    segs = {(s.type_camion, s.periode.nom): s.indices for s in segmenter(clients, JOURNEE)}
    assert segs == {("hayon", "journee"): [2], ("normal", "journee"): [0, 1]}


def test_dimensionnement_reserve_et_flotte():
    clients = [c("08:00", "09:00")] * 35 + [c("08:00", "09:00", "hayon")] * 3
    segs = segmenter(clients, JOURNEE)
    dimensionner(segs, max_clients=15, flotte={"normal": 10, "hayon": 0})
    assert {s.type_camion: s.nb_vehicules for s in segs} == {"normal": 4, "hayon": 0}   # 3 + 1 réserve
    dimensionner(segs, max_clients=15, flotte={"normal": 2, "hayon": 4})
    assert {s.type_camion: s.nb_vehicules for s in segs} == {"normal": 2, "hayon": 2}   # borné par la flotte


def test_budget_respecte_la_limite_de_10_s():
    budgets = repartir_budget([30, 8, 2], 9.0)
    assert sum(budgets) <= 9.01 and all(b >= 0.5 for b in budgets)
    assert budgets[0] > budgets[1] > budgets[2]
    assert repartir_budget([], 9.0) == []
