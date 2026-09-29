from app.services.distances import LYON_CENTRE, haversine_km
from app.services.sectorisation import COURONNE, PERIURBAIN, URBAIN, zone_pour


def test_haversine_lyon_villefranche():
    # Bellecour -> Villefranche-sur-Saône : environ 30 km à vol d'oiseau
    assert 27 < haversine_km(*LYON_CENTRE, 45.9897, 4.7186) < 30


def test_haversine_nulle():
    assert haversine_km(*LYON_CENTRE, *LYON_CENTRE) == 0


def test_zones():
    assert zone_pour("69003", 45.76, 4.85) is URBAIN
    assert zone_pour("69160", 45.763, 4.78) is COURONNE        # Tassin
    assert zone_pour("69400", 45.9897, 4.7186) is PERIURBAIN   # Villefranche
    assert all(10 <= z.max_clients <= 20 for z in (URBAIN, COURONNE, PERIURBAIN))
