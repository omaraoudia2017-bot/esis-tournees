"""Énumérations métier (stockées en types ENUM natifs PostgreSQL)."""
import enum

from sqlalchemy import Enum as SAEnum


class RoleUtilisateur(str, enum.Enum):
    LIVREUR = "livreur"
    ADMIN = "admin"


class TypeCamion(str, enum.Enum):
    NORMAL = "normal"   # colis standards
    HAYON = "hayon"     # palettes (clients professionnels)


class TypeClient(str, enum.Enum):
    PARTICULIER = "particulier"
    PROFESSIONNEL = "professionnel"


class SourceClient(str, enum.Enum):
    IMPORT_CSV = "import_csv"
    SAISIE_MANUELLE = "saisie_manuelle"   # Module 3 : ajout à la volée
    OCR = "ocr"                           # Module 4 : scan


class StatutTournee(str, enum.Enum):
    PLANIFIEE = "planifiee"
    EN_COURS = "en_cours"
    TERMINEE = "terminee"


class StatutLivraison(str, enum.Enum):
    A_LIVRER = "a_livrer"
    EN_COURS = "en_cours"
    LIVRE = "livre"
    ABSENT = "absent"
    REFUSE = "refuse"
    REPORTE = "reporte"


def pg_enum(enum_cls: type[enum.Enum], nom: str) -> SAEnum:
    """Stocke la *valeur* ('livreur') et non le nom Python ('LIVREUR')."""
    return SAEnum(enum_cls, name=nom, values_callable=lambda e: [m.value for m in e])


# Instance partagée : le même type ENUM PostgreSQL sert aux tables clients et vehicules
TYPE_CAMION_PG = pg_enum(TypeCamion, "type_camion")
