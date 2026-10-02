"""Écriture dans le journal de sécurité (source du futur modèle de détection d'anomalies)."""
import ipaddress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import JournalSecurite


@dataclass
class ContexteRequete:
    adresse_ip: str | None = None
    user_agent: str | None = None


def _ip_valide(valeur: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address((valeur or "").strip()))
    except ValueError:
        return None


def contexte_depuis(request: Request) -> ContexteRequete:
    """IP et navigateur du client. X-Forwarded-For n'est cru que s'il vient du réseau interne
    (interface Streamlit ou reverse proxy), jamais d'un client externe qui pourrait le falsifier."""
    pair = _ip_valide(request.client.host if request.client else None)
    ip = pair
    transmis = request.headers.get("x-forwarded-for")
    if transmis and pair and ipaddress.ip_address(pair).is_private:
        ip = _ip_valide(transmis.split(",")[0]) or pair
    return ContexteRequete(adresse_ip=ip, user_agent=(request.headers.get("user-agent") or "")[:255] or None)


def journaliser(db: Session, evenement: str, succes: bool, ctx: ContexteRequete | None = None,
                utilisateur_id: int | None = None, identifiant_saisi: str | None = None,
                **details) -> None:
    """Ajoute une ligne au journal. L'appelant fait le commit (jamais de mot de passe dans details)."""
    ctx = ctx or ContexteRequete()
    db.add(JournalSecurite(
        evenement=evenement,
        succes=succes,
        utilisateur_id=utilisateur_id,
        identifiant_saisi=(identifiant_saisi or "")[:50] or None,
        adresse_ip=ctx.adresse_ip,
        user_agent=ctx.user_agent,
        details=details or None,
    ))


DELAI_CONSULTATION = timedelta(hours=1)


def tracer_consultation(db: Session, utilisateur, ctx: ContexteRequete | None, tournee_id: int) -> None:
    """Accès aux données sensibles d'une tournée (digicodes, téléphones, notes) : tracé au plus une
    fois par heure, par compte et par tournée (l'interface relit la tournée à chaque clic)."""
    depuis = datetime.now(timezone.utc) - DELAI_CONSULTATION
    deja = db.scalar(select(JournalSecurite.id).where(
        JournalSecurite.utilisateur_id == utilisateur.id,
        JournalSecurite.evenement == "CONSULTATION_TOURNEE",
        JournalSecurite.horodatage >= depuis,
        JournalSecurite.details["tournee_id"].as_integer() == tournee_id).limit(1))
    if deja is None:
        journaliser(db, "CONSULTATION_TOURNEE", True, ctx, utilisateur.id, utilisateur.identifiant,
                    tournee_id=tournee_id)
        db.commit()
