"""Fonctions d'aide communes aux tests d'API (comptes, connexion 2FA)."""
from datetime import datetime, timezone

import pyotp

from app.core.config import get_settings
from app.core.security import hacher_mdp
from app.models import Utilisateur
from app.models.enums import RoleUtilisateur

MDP = "MotDePasseTest2026"


def creer_compte(db, identifiant, role=RoleUtilisateur.LIVREUR, secret=None,
                 vehicule_id=None, conditions_acceptees=True) -> Utilisateur:
    """Par défaut, les conditions d'utilisation en vigueur sont déjà acceptées (testées à part
    dans test_conditions.py) : les autres tests n'ont pas à passer par cet écran."""
    u = Utilisateur(identifiant=identifiant, nom="TEST", prenom="Test", role=role,
                    mot_de_passe_hash=hacher_mdp(MDP), totp_secret=secret, totp_active=bool(secret),
                    vehicule_id=vehicule_id,
                    conditions_version=get_settings().conditions_version if conditions_acceptees else None,
                    conditions_acceptees_le=datetime.now(timezone.utc) if conditions_acceptees else None)
    db.add(u)
    db.commit()
    return u


def connecter(client, identifiant, secret) -> str:
    r = client.post("/auth/connexion", json={"identifiant": identifiant, "mot_de_passe": MDP})
    assert r.status_code == 200 and r.json()["etape"] == "verification"
    preauth = {"Authorization": f"Bearer {r.json()['jeton_preauth']}"}
    r = client.post("/auth/2fa/verification", json={"code": pyotp.TOTP(secret).now()}, headers=preauth)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def entete(jeton):
    return {"Authorization": f"Bearer {jeton}"}
