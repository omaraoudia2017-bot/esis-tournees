"""Remarques laissées par les livreurs sur une adresse, pour les prochaines livraisons.

Exemples : « portail fermé le matin, sonner au 2e », « colis à déposer au gardien ».
Le texte est libre : il peut contenir des informations personnelles, il est donc chiffré (Fernet).
Une remarque est rattachée au client (et non à la livraison) : elle suit l'adresse d'une tournée
à l'autre. Elle est supprimée avec l'anonymisation du client (droit à l'effacement).
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import EncryptedText


class RemarqueClient(Base):
    __tablename__ = "remarques_clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[str] = mapped_column(
        String(10), ForeignKey("clients.id_client", ondelete="CASCADE"), index=True
    )
    # SET NULL : la remarque reste utile si le compte de son auteur est supprimé un jour
    auteur_id: Mapped[int | None] = mapped_column(ForeignKey("utilisateurs.id", ondelete="SET NULL"))
    texte: Mapped[str] = mapped_column(EncryptedText)
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
