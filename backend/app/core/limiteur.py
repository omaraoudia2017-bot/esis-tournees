"""Limitation du nombre de requêtes (fenêtre glissante, en mémoire).

Suffisant pour un seul processus API. Avec plusieurs instances, il faudrait un stockage
partagé (Redis) : écarté ici volontairement (cahier des charges, tableau 4).
"""
import threading
import time
from collections import defaultdict, deque


class LimiteurRequetes:
    def __init__(self, max_requetes: int, fenetre_s: float = 60.0):
        self.max_requetes = max_requetes
        self.fenetre_s = fenetre_s
        self._appels: dict[str, deque] = defaultdict(deque)
        self._verrou = threading.Lock()

    def autoriser(self, cle: str) -> bool:
        maintenant = time.monotonic()
        with self._verrou:
            appels = self._appels[cle]
            while appels and appels[0] <= maintenant - self.fenetre_s:
                appels.popleft()
            if len(appels) >= self.max_requetes:
                return False
            appels.append(maintenant)
            return True

    def reinitialiser(self) -> None:
        with self._verrou:
            self._appels.clear()
