# ESIS — Optimisation des tournées de livraison

Application de gestion et d'optimisation des tournées (FastAPI, PostgreSQL, OR-Tools, Scikit-Learn, Streamlit).

## Démarrage (Windows / PowerShell)

```powershell
Copy-Item .env.example .env          # puis remplir les valeurs "a_generer"
docker compose up -d --build
docker compose exec api python -m app.db.init_db
```

- API : http://localhost:8000/health
- Documentation interactive (dev uniquement) : http://localhost:8000/docs

## Arborescence

```
backend/app/core     configuration, chiffrement Fernet
backend/app/db       connexion, types chiffrés, triggers, création des tables
backend/app/models   modèles SQLAlchemy
backend/app/api      routes (à venir)
frontend/            interface Streamlit (à venir)
data/                échantillons CSV — non versionné (données personnelles)
docs/                documentation technique
```

## Données personnelles

Les fichiers de `data/` contiennent des données clients réelles : ils sont exclus de Git.
Digicodes, téléphones, notes de livraison et secrets TOTP sont chiffrés en base (Fernet).
