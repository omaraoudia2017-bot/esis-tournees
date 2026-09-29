# ESIS — Optimisation des tournées de livraison

Application de gestion et d'optimisation des tournées (FastAPI, PostgreSQL, OR-Tools, Scikit-Learn, Streamlit).

## Démarrage (Windows / PowerShell)

```powershell
Copy-Item .env.example .env          # puis remplir les valeurs "a_generer"
docker compose up -d --build
docker compose exec api python -m app.db.init_db
```

### Données

```powershell
docker compose exec api python -m scripts.importer_csv          # import + nettoyage de data/raw/tournees_esis.csv
docker compose exec api python -m scripts.geocoder_clients      # géocodage Nominatim (cache) + zones
docker compose exec api python -m scripts.generer_jeu_simule    # 5000 clients simulés + flotte
docker compose exec api python -m pytest -q                     # tests unitaires
```

### Optimisation

```powershell
docker compose exec api python -m scripts.tester_optimisation                          # 20 clients réels
docker compose exec api python -m scripts.tester_optimisation --source simulation --nombre 20
docker compose exec api python -m scripts.planifier_tournee                            # CVRPTW : créneaux, service, effectif
docker compose exec api python -m scripts.planifier_tournee --zone "Première couronne" --nombre 30
```

### Comptes

```powershell
docker compose exec api python -m scripts.gerer_utilisateurs creer --identifiant admin.omar --nom AOUDIA --prenom Omar --role admin
docker compose exec api python -m scripts.gerer_utilisateurs lister
```

- Interface : http://localhost:8501
- API : http://localhost:8000/health
- Documentation interactive (dev uniquement) : http://localhost:8000/docs

## Arborescence

```
backend/app/core     configuration, chiffrement Fernet
backend/app/db       connexion, types chiffrés, triggers, création des tables
backend/app/models   modèles SQLAlchemy
backend/app/services nettoyage, géocodage, sectorisation, distances, authentification
backend/app/optim    matrice des trajets et solveur OR-Tools
backend/scripts      import CSV, géocodage, jeu simulé
backend/tests        tests unitaires (pytest)
backend/app/api      routes (authentification, administration)
backend/app/schemas  schémas Pydantic
frontend/            interface Streamlit
data/                échantillons CSV — non versionné (données personnelles)
docs/                documentation technique
```

## Données personnelles

Les fichiers de `data/` contiennent des données clients réelles : ils sont exclus de Git.
Digicodes, téléphones, notes de livraison et secrets TOTP sont chiffrés en base (Fernet).
