"""Point d'entrée de l'API FastAPI."""
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api import routes_admin, routes_auth, routes_conditions, routes_tournees
from app.core.config import get_settings
from app.db.session import get_db

settings = get_settings()
est_dev = settings.environment == "dev"

app = FastAPI(
    title=settings.app_name,
    version="0.4.0",
    # Documentation Swagger désactivée hors développement (surface d'attaque réduite)
    docs_url="/docs" if est_dev else None,
    redoc_url=None,
    openapi_url="/openapi.json" if est_dev else None,
)

# CORS restreint : par défaut aucune origine (l'interface Streamlit appelle l'API côté serveur)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def entetes_securite(request: Request, call_next):
    reponse = await call_next(request)
    reponse.headers["X-Content-Type-Options"] = "nosniff"
    reponse.headers["X-Frame-Options"] = "DENY"
    reponse.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith(("/auth", "/moi", "/tournees", "/livraisons", "/remarques", "/conditions",
                                     "/admin")):
        # jamais de jeton ni de donnée client (digicode, téléphone) dans un cache
        reponse.headers["Cache-Control"] = "no-store"
    return reponse


app.include_router(routes_auth.router)
app.include_router(routes_conditions.router)
app.include_router(routes_admin.router)
app.include_router(routes_tournees.router)


@app.get("/health", tags=["Supervision"])
def health(db: Session = Depends(get_db)) -> dict:
    """Vérifie que l'API répond et que PostgreSQL est joignable."""
    try:
        db.execute(text("SELECT 1"))
    except OperationalError:
        raise HTTPException(status_code=503, detail="Base de données injoignable")
    return {"status": "ok", "database": "ok", "environment": settings.environment}
