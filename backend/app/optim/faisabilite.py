"""Faisabilité d'une tournée pour le livreur, AVANT l'optimisation géographique.

Une tournée peut respecter la limite de la zone et rester impossible à tenir : 19 clients dont les
créneaux sont tous le matin, par exemple. Trois règles simples, expliquables au planificateur :

1. Charge par heure : au plus `par_heure` clients (2 par défaut) prévus dans une même heure
   (8 h – 9 h, 9 h – 10 h…). Chaque client est placé dans une heure de son créneau, au plus tôt,
   en traitant d'abord les créneaux qui finissent le plus tôt (règle « échéance la plus proche »,
   optimale pour ce type de placement).
2. Limite selon la période : `par_heure` × nombre d'heures de la période, sans dépasser la limite de
   la zone. Avec 2 clients par heure : matin (7 h – 14 h) 14 clients, après-midi 12, soirée 10 ;
   seule une tournée de la journée complète peut atteindre 20 clients.
3. Heure de départ du dépôt au plus tard : calculée à rebours depuis le dernier client, pour que
   chaque client soit servi avant la fin de son créneau (attente possible si on arrive en avance).

Fonctions pures, testées dans tests/test_faisabilite.py.
"""
import math

from app.optim.matrice import MatriceTrajets
from app.optim.probleme import ClientTournee
from app.optim.segmentation import Periode

LIMITE_MIN_BASE = 10          # contrainte de la base : une tournée compte 10 à 20 clients au maximum


def heures_possibles(client: ClientTournee, periode: Periode) -> list[int]:
    """Heures pleines (8 = 8 h – 9 h) qui chevauchent le créneau du client dans la période.
    Créneau hors période : toutes les heures de la période (le client a été rattaché à elle)."""
    debut, fin = max(client.debut_s, periode.depart_s), min(client.fin_s, periode.fin_s)
    if fin <= debut:
        debut, fin = periode.depart_s, periode.fin_s
    return list(range(debut // 3600, math.ceil(fin / 3600)))


def limite_periode(periode: Periode, limite_zone: int, par_heure: int) -> int:
    heures = math.ceil((periode.fin_s - periode.depart_s) / 3600)
    return min(limite_zone, max(LIMITE_MIN_BASE, par_heure * heures))


def ordre_echeance(clients: list[ClientTournee]) -> list[int]:
    """Indices triés par fin de créneau puis début (les plus pressés d'abord)."""
    return sorted(range(len(clients)), key=lambda i: (clients[i].fin_s, clients[i].debut_s))


def placer_par_heure(clients: list[ClientTournee], periode: Periode,
                     par_heure: int) -> tuple[dict[int, int], list[int]]:
    """Retourne ({indice client: heure visée}, [indices impossibles à placer])."""
    charge: dict[int, int] = {}
    places, refuses = {}, []
    for i in ordre_echeance(clients):
        heure = next((h for h in heures_possibles(clients[i], periode) if charge.get(h, 0) < par_heure), None)
        if heure is None:
            refuses.append(i)
        else:
            charge[heure] = charge.get(heure, 0) + 1
            places[i] = heure
    return places, refuses


def charge_par_heure(heures: list[int]) -> dict[int, int]:
    charge: dict[int, int] = {}
    for h in heures:
        charge[h] = charge.get(h, 0) + 1
    return dict(sorted(charge.items()))


def depart_au_plus_tard_s(matrice: MatriceTrajets, clients: list[ClientTournee]) -> int | None:
    """Heure (en secondes depuis minuit) à laquelle il faut quitter le dépôt au plus tard pour servir
    `clients` DANS CET ORDRE sans dépasser la fin d'aucun créneau. Nœud 0 de la matrice = dépôt,
    nœud k = k-ième client de la liste. Calcul à rebours : dernier début de service possible chez
    chaque client, en tenant compte du temps sur place et du trajet vers le suivant."""
    if not clients:
        return None
    limite = None
    for k in range(len(clients), 0, -1):
        c = clients[k - 1]
        dernier = c.fin_s
        if limite is not None:
            dernier = min(dernier, limite - c.temps_service_s - matrice.durees_s[k][k + 1])
        limite = dernier
    return limite - matrice.durees_s[0][1]
