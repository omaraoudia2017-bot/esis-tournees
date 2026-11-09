# Documentation technique — Road Optim, optimisation des tournées de livraison

Public : développeur ou administrateur système chargé d'installer, d'exploiter ou de faire évoluer
l'application. Le guide des utilisateurs (livreur, planificateur) est dans `docs/GUIDE_UTILISATEUR.md`,
les commandes courantes dans `docs/COMMANDES.md`, les résultats des tests dans `docs/RAPPORT_TESTS.md`.

## 1. Vue d'ensemble

L'application organise les tournées de livraison de l'entreprise : le planificateur importe les
clients et attribue les tournées, le livreur obtient sur son smartphone un ordre de passage optimisé
(créneaux horaires, type de camion, limite d'effectif) et pointe chaque livraison ; les temps mesurés
alimentent un modèle d'apprentissage qui affine les estimations suivantes.

```
            Navigateur (smartphone du livreur, poste du planificateur)
                                  │ HTTPS (production)
                         ┌────────▼────────┐
                         │  Caddy (proxy)  │  certificat TLS, en-têtes de sécurité
                         └────────┬────────┘
                                  │ réseau interne Docker
                         ┌────────▼────────┐
                         │ Interface       │  Streamlit : écrans livreur et administrateur
                         │ (esis_front)    │  jeton de session conservé côté serveur
                         └────────┬────────┘
                                  │ HTTP interne + jeton JWT
                         ┌────────▼────────┐
                         │ API (esis_api)  │  FastAPI : authentification, règles métier,
                         │                 │  OR-Tools (CVRPTW), scikit-learn (IA)
                         └────────┬────────┘
                                  │ SQL (SQLAlchemy)
                         ┌────────▼────────┐
                         │ PostgreSQL 16   │  données, triggers d'intégrité, journal
                         │ (esis_db)       │  champs sensibles chiffrés (Fernet)
                         └─────────────────┘
```

| Composant | Technologie | Rôle |
|---|---|---|
| Base de données | PostgreSQL 16 | clients, comptes, tournées, livraisons, journal de sécurité |
| API | Python 3.11, FastAPI, SQLAlchemy 2, Pydantic | sécurité, règles métier, calculs |
| Optimisation | Google OR-Tools 9.11 (routage) | ordre de passage sous contraintes (CVRPTW) |
| Apprentissage | scikit-learn (Random Forest, Isolation Forest) | temps de service, connexions anormales |
| Interface | Streamlit, Folium (cartes OpenStreetMap) | écrans livreur et administrateur |
| Géocodage | Nominatim (OpenStreetMap) via geopy | adresse → coordonnées GPS, avec cache en base |
| Conteneurs | Docker Compose | même environnement en développement et en production |
| HTTPS | Caddy 2 | certificat automatique, en-têtes de sécurité |

## 2. Arborescence

```
esis-tournees/
├── docker-compose.yml           développement (code monté, rechargement à chaud)
├── docker-compose.prod.yml      production (à superposer : HTTPS, aucun port interne publié)
├── .env.example                 modèle de configuration (le vrai .env n'est jamais versionné)
├── deploiement/Caddyfile        proxy HTTPS
├── exploitation/                sauvegarder.ps1, restaurer.ps1
├── backend/
│   ├── app/
│   │   ├── main.py              application FastAPI, en-têtes de sécurité
│   │   ├── core/                configuration, sécurité (bcrypt, JWT, TOTP), chiffrement, limiteur
│   │   ├── db/                  connexion, type chiffré, triggers et migrations (ddl.py), init_db
│   │   ├── models/              tables SQLAlchemy
│   │   ├── schemas/             validation des entrées / sorties (Pydantic)
│   │   ├── api/                 routes : auth, tournées du livreur, administration
│   │   ├── services/            logique métier (auth, tournées, admin, import, géocodage, journal…)
│   │   ├── optim/               matrice des trajets, solveur CVRPTW, segmentation
│   │   └── ml/                  variables, temps de service, détection d'anomalies
│   ├── scripts/                 outils en ligne de commande (import, comptes, entraînement, audits…)
│   ├── tests/                   146 tests automatisés (pytest)
│   └── modeles/                 modèles entraînés *.joblib (hors Git)
├── frontend/                    app.py (connexion, livreur), espace_admin.py (planificateur)
├── data/                        échantillons réels (hors Git : données personnelles)
└── docs/                        documentation, rapport de tests, preuves d'audit
```

## 3. Installation sur un nouveau poste ou serveur

**Prérequis** : Docker Desktop (Windows / macOS) ou Docker Engine + plugin Compose (Linux), Git,
accès Internet sortant (images Docker, bibliothèques Python, Nominatim).

1. Récupérer le code : `git clone <dépôt> esis-tournees` puis `cd esis-tournees`.
2. Créer la configuration : copier `.env.example` en `.env`, puis renseigner :
   - `POSTGRES_PASSWORD`, `JWT_SECRET_KEY` : valeurs aléatoires longues ;
   - `FERNET_KEY` : clé de chiffrement Fernet ;
   - `DEPOT_NOM`, `DEPOT_LATITUDE`, `DEPOT_LONGITUDE` : entrepôt de départ ;
   - en production : `ESIS_DOMAINE` (nom de domaine), `ESIS_PORT_HTTPS=443`, `ESIS_PORT_HTTP=80`.

   Générer les secrets (après une première construction de l'image de l'API) :
   ```
   docker compose build api
   docker compose run --rm --no-deps api python -c "import secrets; print(secrets.token_urlsafe(48))"
   docker compose run --rm --no-deps api python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   **La clé `FERNET_KEY` doit être conservée à part** (gestionnaire de mots de passe) : sans elle, les
   digicodes, téléphones, notes et secrets 2FA de la base et des sauvegardes sont illisibles. Ne
   jamais la changer sur une base existante.
3. Démarrer : `docker compose up -d --build` (développement) ou
   `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build` (production).
4. Créer les tables, triggers et migrations : `docker compose exec api python -m app.db.init_db`
   (idempotent : peut être relancé à chaque mise à jour).
5. Créer le premier administrateur (mot de passe saisi au clavier) :
   `docker compose exec api python -m scripts.gerer_utilisateurs creer --identifiant admin.prenom --nom NOM --prenom Prénom --role admin`.
   La double authentification est imposée à sa première connexion (QR code).
6. Données : importer le fichier clients depuis l'espace administrateur (rubrique Import CSV), ou en
   ligne de commande `scripts.importer_csv` puis `scripts.geocoder_clients`. Les zones sont créées
   au géocodage. Pour une démonstration : `scripts.generer_jeu_simule` (5 000 clients et 14 camions
   simulés), puis `scripts.entrainer_modele` et `scripts.entrainer_anomalies`.

Adresses : développement http://localhost:8501 (interface), http://localhost:8000/docs (API,
développement uniquement) ; production https://<ESIS_DOMAINE> (seul point d'entrée).

## 4. Données

| Table | Contenu | Points notables |
|---|---|---|
| `utilisateurs` | comptes livreur / admin | hash bcrypt, secret TOTP chiffré, verrouillage, `sessions_invalidees_le` |
| `codes_secours` | codes de secours 2FA | seule l'empreinte HMAC est stockée, usage unique |
| `jetons_revoques` | sessions fermées par déconnexion | conservées jusqu'à expiration |
| `clients` | fiches clients | téléphone, digicode, notes **chiffrés** ; créneau ; étage / ascenseur ; type de camion ; précision du géocodage |
| `zones`, `zones_codes_postaux` | sectorisation | limite d'effectif 10 à 20 clients par tournée |
| `vehicules` | flotte | camion normal (colis) ou hayon (palettes) |
| `tournees`, `livraisons` | tournée du jour, arrêts | ordre, heure prévue, pointage, temps réel **calculé par PostgreSQL**, temps prédit |
| `journal_securite` | traces de sécurité | **ajout seul** (trigger), suppression possible après un an seulement |
| `cache_geocodage` | réponses Nominatim | aucune adresse demandée deux fois |

Règles appliquées par la base elle-même (`app/db/ddl.py`) : limite d'effectif d'une tournée
(trigger avec verrou), journal non modifiable, contraintes de format (identifiant client à
10 chiffres, code postal, créneau cohérent, coordonnées valides). Les évolutions de schéma sont des
migrations idempotentes (`ALTER … IF NOT EXISTS`) appliquées par `init_db`.

## 5. API

Toutes les routes, sauf `/health` et la connexion, exigent un jeton de session valide (30 min).
Le rôle est relu en base à chaque requête.

| Rôle | Routes |
|---|---|
| Public | `GET /health` ; `POST /auth/connexion` (identifiant + mot de passe → jeton de pré-authentification de 5 min) ; `POST /auth/2fa/enrolement`, `/auth/2fa/activation`, `/auth/2fa/verification` |
| Connecté | `GET /auth/moi` ; `POST /auth/deconnexion` |
| Livreur | `GET /tournees/du-jour` ; `POST /tournees/du-jour/optimiser` ; `POST /tournees/du-jour/clients` (adresse à la volée) ; `POST /livraisons/{id}/demarrer` et `/terminer` |
| Administrateur | comptes : `GET/POST /admin/utilisateurs`, `POST /admin/utilisateurs/{id}/{desactiver, reactiver, deverrouiller, reinitialiser-mdp, reinitialiser-2fa, vehicule}`, `GET /admin/vehicules` ; données : `POST /admin/import-csv`, `POST /admin/clients/geocoder` ; tournées : `GET/POST /admin/tournees`, `GET /admin/tournees/{id}`, `POST /admin/tournees/{id}/clients` ; clients : `GET /admin/clients`, `POST /admin/clients/{id}/correction`, RGPD `GET /admin/clients/{id}/export` et `POST /admin/clients/{id}/anonymiser` ; suivi : `GET /admin/tableau-de-bord`, `GET /admin/journal-securite` |

La documentation interactive (Swagger) est disponible sur `/docs` en développement uniquement.

## 6. Optimisation des tournées (CVRPTW)

Problème de tournées de véhicules avec capacité et fenêtres horaires, résolu par OR-Tools :
- matrice des trajets : distance à vol d'oiseau (Haversine) × `COEFFICIENT_ROUTIER` (1,3), vitesse
  `VITESSE_MOYENNE_KMH` (30 km/h) ;
- capacité : limite d'effectif de la zone (contrainte stricte) ;
- créneaux : contrainte souple (retard autorisé mais pénalisé : `PENALITE_RETARD_PAR_SECONDE`) ;
- flotte hétérogène : un client palette n'est servi que par un camion hayon (contrainte stricte) ;
- client impossible : laissé de côté avec une forte pénalité et marqué « à reporter » ;
- recherche locale guidée, arrêtée après `OPTIM_TEMPS_LIMITE_S` (5 s ; solution stable en moins de 1 s) ;
- journée complète : segmentation par type de camion et période (matin, après-midi, soirée), budget
  global `OPTIM_BUDGET_TOTAL_S` (9 s) ;
- en cours de tournée : seul l'itinéraire restant est recalculé, depuis le dernier client servi.

## 7. Apprentissage automatique

| Modèle | Fichier | Entraînement | Repli si absent ou illisible |
|---|---|---|---|
| Temps de service (Random Forest, 29 variables, aucune donnée personnelle) | `backend/modeles/temps_service.joblib` | `scripts.entrainer_modele` (historique simulé + pointages réels plausibles de 1 à 90 min) | valeurs fixes 10 min (colis) / 20 min (palette) |
| Connexions anormales (Isolation Forest, 10 variables) | `backend/modeles/anomalies.joblib` | `scripts.entrainer_anomalies` | aucune analyse ; la connexion n'est jamais bloquée |

Les modèles sont rechargés automatiquement quand leur fichier change. Les fichiers `*.joblib` sont
produits par l'application elle-même et exclus de Git. Réentraîner le modèle de temps de service
quand les pointages réels s'accumulent (par exemple chaque mois). L'historique de démonstration
(`scripts.generer_historique_demo`) n'est jamais utilisé pour l'entraînement.

## 8. Sécurité

| Exigence | Mise en œuvre |
|---|---|
| Mots de passe | bcrypt (coût 12), politique de 12 caractères minimum, verrouillage 15 min après 5 échecs, message d'erreur identique pour un compte inconnu |
| Double authentification | TOTP (application d'authentification), anti-rejeu, 10 codes de secours à usage unique |
| Sessions | JWT signé de 30 min ; révocation à la déconnexion ; coupure immédiate en cas de désactivation ou de réinitialisation |
| Contrôle d'accès | deux rôles vérifiés côté serveur ; un livreur ne voit que sa tournée (404 identique à une ressource inexistante) |
| Données sensibles | digicodes, téléphones, notes, secrets 2FA chiffrés en base (Fernet) ; absents des listes de l'administrateur |
| Traçabilité | journal en ajout seul : connexions, échecs, refus d'accès, actions d'administration, consultations des digicodes, exports et anonymisations RGPD |
| Détection | Isolation Forest sur les connexions, alertes dans l'espace administrateur |
| Réseau | production : HTTPS seul exposé, en-têtes de sécurité, CORS fermé, pas de cache sur les données sensibles |
| RGPD | minimisation (pas de suivi GPS des livreurs), export et anonymisation d'un client, date de dernière livraison conservée pour la durée de conservation |
| Code et dépendances | Bandit et pip-audit (voir `docs/RAPPORT_TESTS.md`) |

## 9. Exploitation

**Démarrer, arrêter** : `docker compose up -d` / `docker compose down` (développement), même chose
avec `-f docker-compose.yml -f docker-compose.prod.yml` en production. Ne jamais utiliser
`down -v`, qui supprime le volume de la base.

**Journaux techniques** : `docker compose logs -f api` (ou `frontend`, `db`, `caddy`).

**Sauvegarde** (Windows, PowerShell, depuis la racine du projet) :
```
powershell -ExecutionPolicy Bypass -File exploitation\sauvegarder.ps1
```
Format compressé `pg_dump`, dans `Documents\ESIS_sauvegardes` (hors du projet), empreinte SHA-256,
14 sauvegardes conservées. Planification quotidienne à 22 h 30 (Planificateur de tâches Windows) :
```
schtasks /Create /SC DAILY /ST 22:30 /TN "ESIS sauvegarde" /TR "powershell -ExecutionPolicy Bypass -File \"C:\Dev\esis-tournees\exploitation\sauvegarder.ps1\""
```
Copier régulièrement le dossier des sauvegardes sur un support externe chiffré (règle 3-2-1 :
3 copies, 2 supports, 1 hors site). Les sauvegardes contiennent des données personnelles : jamais
dans Git ni dans un dossier partagé.

**Test de restauration** (chaque semaine, sans risque pour la base en service) :
```
powershell -ExecutionPolicy Bypass -File exploitation\restaurer.ps1 -Fichier <sauvegarde.dump> -Test
```
Vérifie l'empreinte, restaure dans une base temporaire, compare le nombre de lignes, puis la supprime.
Test réalisé le 2 octobre 2026 : 5 204 clients, 8 comptes, 44 tournées, 516 livraisons, 72 traces,
identiques à la base en service.

**Restauration réelle** (après incident) : même commande sans `-Test` ; confirmation « RESTAURER »
exigée ; l'API et l'interface sont arrêtées pendant l'opération.

**Comptes** : mot de passe oublié d'un livreur → espace administrateur ; d'un administrateur →
`scripts.gerer_utilisateurs reinitialiser-mdp --identifiant <compte>` ; téléphone perdu →
`reinitialiser-2fa`.

**Mise à jour** : `git pull`, `docker compose … up -d --build`, `python -m app.db.init_db`, tests
(`python -m pytest -q`). Avant de changer une bibliothèque : `scripts.resoudre_versions`, puis
pip-audit et les tests.

**RGPD — durée de conservation** : la date de dernière livraison de chaque client est enregistrée ;
la purge des clients inactifs et des traces du journal de plus d'un an (seules supprimables par le
trigger) reste à automatiser selon la durée fixée au registre des traitements de l'entreprise.

## 10. Tests

`docker compose exec api python -m pytest -q` : 146 tests sur la vraie base, dans une transaction
annulée (aucune donnée modifiée). Audits et performance : section « Audits » de `docs/COMMANDES.md`.
Résultats détaillés : `docs/RAPPORT_TESTS.md`.
