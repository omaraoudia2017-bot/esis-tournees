# Aide-mémoire des commandes — Projet ESIS

Toutes les commandes se lancent dans **PowerShell**, depuis le dossier du projet.
Astuce : dans VS Code, `Ctrl + Shift + V` affiche ce fichier en mode lecture.

```powershell
cd "C:\Users\iegin\Desktop\ESIS\Projet Final\esis-tournees"
```

---

## 1. Démarrer et arrêter l'application

Démarrer Docker Desktop (s'il n'est pas lancé) :

```powershell
Start-Process "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"
```

Démarrer les 3 conteneurs (base, API, interface) :

```powershell
docker compose up -d
```

Vérifier que tout tourne :

```powershell
docker compose ps
```

Vérifier que l'API et la base répondent :

```powershell
curl.exe http://localhost:8000/health
```

Arrêter les conteneurs (les données sont conservées) :

```powershell
docker compose down
```

Reconstruire après un changement de `requirements.txt` ou de `Dockerfile` :

```powershell
docker compose up -d --build
```

Redémarrer un seul service (ici l'interface) :

```powershell
docker compose restart frontend
```

## 2. Adresses utiles

| Quoi | Adresse |
|---|---|
| Application (Streamlit) | http://localhost:8501 |
| Documentation de l'API (Swagger) | http://localhost:8000/docs |
| État de l'API | http://localhost:8000/health |

## 3. Voir ce qui se passe (logs)

Logs de l'API en direct (`Ctrl + C` pour quitter) :

```powershell
docker compose logs -f api
```

Logs de l'interface :

```powershell
docker compose logs -f frontend
```

Les 50 dernières lignes de tous les services :

```powershell
docker compose logs --tail 50
```

## 4. Tests

Tous les tests (89 attendus) :

```powershell
docker compose exec api python -m pytest -q
```

Tests de la tournée et de l'ajout d'adresse, en détail (idéal pour une démo) :

```powershell
docker compose exec api python -m pytest tests/test_tournees_api.py tests/test_ajout_volee.py -v
```

Tests de sécurité et d'authentification :

```powershell
docker compose exec api python -m pytest tests/test_securite.py tests/test_auth_api.py -v
```

Tests de l'optimisation :

```powershell
docker compose exec api python -m pytest tests/test_optim.py tests/test_cvrptw.py tests/test_segmentation.py -v
```

Un seul test par son nom :

```powershell
docker compose exec api python -m pytest -k cloisonnement -v
```

## 5. Démonstration

Donner une tournée neuve à `livreur.demo` (12 clients, matin) :

```powershell
docker compose exec api python -m scripts.attribuer_tournee --livreur livreur.demo --remplacer
```

Même chose l'après-midi (heures de passage dans le futur pendant une démo) :

```powershell
docker compose exec api python -m scripts.attribuer_tournee --livreur livreur.demo --periode apres-midi --remplacer
```

Choisir la zone et le nombre de clients :

```powershell
docker compose exec api python -m scripts.attribuer_tournee --livreur livreur.demo --zone "Première couronne" --nombre 10 --remplacer
```

Adresse à saisir pour tester l'ajout en cours de tournée : `10 rue de la République`, `69002`, `Lyon`.

## 6. Comptes utilisateurs

Lister les comptes :

```powershell
docker compose exec api python -m scripts.gerer_utilisateurs lister
```

Débloquer un compte (après 5 mots de passe faux) :

```powershell
docker compose exec api python -m scripts.gerer_utilisateurs deverrouiller --identifiant livreur.demo
```

Téléphone perdu : refaire l'enrôlement 2FA à la prochaine connexion :

```powershell
docker compose exec api python -m scripts.gerer_utilisateurs reinitialiser-2fa --identifiant livreur.demo
```

Créer un livreur avec un mot de passe généré (affiché une seule fois) :

```powershell
docker compose exec api python -m scripts.gerer_utilisateurs creer --identifiant livreur.test --nom TEST --prenom Livreur --role livreur --vehicule SIM-N02 --generer-mdp
```

Créer un administrateur (le mot de passe est demandé au clavier) :

```powershell
docker compose exec api python -m scripts.gerer_utilisateurs creer --identifiant admin.test --nom TEST --prenom Admin --role admin
```

Désactiver un compte :

```powershell
docker compose exec api python -m scripts.gerer_utilisateurs desactiver --identifiant livreur.test
```

## 7. Optimisation en ligne de commande (résultats chiffrés)

Ordre optimisé contre ordre brut, 20 clients simulés :

```powershell
docker compose exec api python -m scripts.tester_optimisation --source simulation --nombre 20
```

Tournées avec créneaux, temps de service et limite d'effectif :

```powershell
docker compose exec api python -m scripts.planifier_tournee
```

Même chose en première couronne, 30 clients :

```powershell
docker compose exec api python -m scripts.planifier_tournee --zone "Première couronne" --nombre 30
```

Journée complète : palettes et colis séparés, matin / après-midi / soirée :

```powershell
docker compose exec api python -m scripts.planifier_journee --details
```

Comparaison avec une journée continue :

```powershell
docker compose exec api python -m scripts.planifier_journee --journee-continue
```

## 8. Base de données

Ouvrir la console PostgreSQL :

```powershell
docker compose exec db psql -U esis -d esis
```

Requêtes utiles une fois dans `psql` (quitter avec `\q`) :

```sql
\dt
SELECT id_client, nom_client, LEFT(digicode, 40) AS digicode_en_base FROM clients WHERE source = 'simulation' AND digicode IS NOT NULL LIMIT 3;
SELECT identifiant, role, LEFT(mot_de_passe_hash, 25) AS hash, totp_active FROM utilisateurs;
SELECT horodatage, evenement, succes, identifiant_saisi FROM journal_securite ORDER BY id DESC LIMIT 10;
SELECT source, count(*) FROM clients GROUP BY source;
SELECT z.nom, z.max_clients, count(c.id_client) FROM zones z LEFT JOIN clients c ON c.zone_id = z.id GROUP BY z.nom, z.max_clients;
```

Preuve que le journal ne peut pas être modifié (la base doit refuser) :

```sql
UPDATE journal_securite SET succes = true WHERE id = (SELECT max(id) FROM journal_securite);
```

Créer ou mettre à jour les tables (sans rien effacer) :

```powershell
docker compose exec api python -m app.db.init_db
```

> **Attention :** `init_db --reset` efface TOUTE la base (clients géocodés, comptes, tournées). Ne pas l'utiliser.

Sauvegarder la base hors du projet (le fichier contient des données personnelles : jamais dans Git) :

```powershell
docker compose exec db pg_dump -U esis -d esis -F c -f /tmp/esis.dump
docker cp esis_db:/tmp/esis.dump "$HOME\Documents\esis_$(Get-Date -Format yyyyMMdd).dump"
```

## 9. Données

Importer le CSV de l'échantillon (`data/raw/tournees_esis.csv`) :

```powershell
docker compose exec api python -m scripts.importer_csv
```

Géocoder les clients sans coordonnées (Nominatim, 1 requête par seconde) :

```powershell
docker compose exec api python -m scripts.geocoder_clients
```

Réessayer les adresses non trouvées :

```powershell
docker compose exec api python -m scripts.geocoder_clients --retenter-echecs
```

Générer le jeu simulé (5 000 clients + flotte de 14 camions) :

```powershell
docker compose exec api python -m scripts.generer_jeu_simule
```

Supprimer le jeu simulé :

```powershell
docker compose exec api python -m scripts.generer_jeu_simule --purger
```

## 10. Git (fin de chaque étape)

Voir ce qui a changé (vérifier que `.env` et `data/` n'apparaissent pas) :

```powershell
git status --short
```

Enregistrer une version :

```powershell
git add .
git commit -m "Jour X : description courte"
```

Historique :

```powershell
git log --oneline
```

Voir les modifications d'un fichier avant de l'enregistrer :

```powershell
git diff backend/app/services/tournees.py
```

## 11. Dépannage

| Problème | Commande ou solution |
|---|---|
| `docker` : erreur « daemon » ou « pipe » | Lancer Docker Desktop (section 1), attendre « Engine running » |
| `git` ou `docker` non reconnu après une installation | `$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")` |
| Accents illisibles dans PowerShell | `[Console]::OutputEncoding = [Text.Encoding]::UTF8` |
| Git : « index.lock exists » | `Remove-Item .git\index.lock` (si aucune autre commande git ne tourne) |
| Compte bloqué | `deverrouiller` (section 6) |
| Page Streamlit cassée, erreur « removeChild » | Chrome : « Ne jamais traduire ce site », puis `Ctrl + F5` |
| L'interface ne change pas après une modification | `docker compose restart frontend`, puis `Ctrl + F5` |
| Autre page sur localhost (ex. Sonarr sur :8989) | L'application ESIS est sur le port **8501** |
| Un conteneur ne démarre pas | `docker compose logs api` (ou `db`, `frontend`) pour lire l'erreur |

> Ne jamais copier le contenu du fichier `.env` dans un message, une capture ou Git.
