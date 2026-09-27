"""Point d'entrée de l'API FastAPI."""
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db

settings = get_settings()
est_dev = settings.environment == "dev"

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    # Documentation Swagger désactivée hors développement (surface d'attaque réduite)
    docs_url="/docs" if est_dev else None,
    redoc_url=None,
    openapi_url="/openapi.json" if est_dev else None,
)


@app.get("/health", tags=["Supervision"])
def health(db: Session = Depends(get_db)) -> dict:
    """Vérifie que l'API répond et que PostgreSQL est joignable."""
    try:
        db.execute(text("SELECT 1"))
    except OperationalError:
        raise HTTPException(status_code=503, detail="Base de données injoignable")
    return {"status": "ok", "database": "ok", "environment": settings.environment}
