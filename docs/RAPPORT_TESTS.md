# Rapport de tests — ESIS, optimisation des tournées de livraison

Campagne du 2 octobre 2026 · version du dépôt après le commit `f820b37` (espace administrateur) ·
environnement Docker Compose (PostgreSQL 16, API FastAPI, interface Streamlit), Python 3.11.

Résultats bruts conservés dans `docs/audit/` : `tests.txt`, `bandit.txt`, `pip-audit-api.txt`,
`pip-audit-front.txt`, `performances.txt`, `versions.txt`.

## 1. Synthèse

| Contrôle | Résultat | Critère |
|---|---|---|
| Tests automatisés (fonctionnels, sécurité, IA) | **146 / 146 réussis** | 100 % |
| Tests d'intrusion (11 scénarios d'attaque) | **11 / 11 refusés proprement** | aucun accès indu |
| Analyse statique du code (Bandit, 5 208 lignes) | **0 problème** (13 faibles avant correction) | aucun problème moyen ou élevé |
| Dépendances de l'API (pip-audit) | **2 vulnérabilités, 1 bibliothèque** (33 dans 6 avant) | risque résiduel justifié |
| Dépendances de l'interface (pip-audit) | **0 vulnérabilité** (22 dans 3 avant) | aucune |
| Optimisation d'une tournée de 20 clients | **solution stable en moins de 1 s** | < 10 s |
| Planification d'une journée de 200 clients | **9,4 s, 200 / 200 servis** | < 10 s |
| Recette des écrans après mise à jour | livreur et administrateur validés | affichage et actions OK |

## 2. Méthode

1. **Tests automatisés (pytest)** sur la vraie base PostgreSQL : chaque test s'exécute dans une
   transaction annulée à la fin, la base n'est jamais modifiée. Le solveur est limité à 1 s et les
   modèles d'IA sont désactivés par défaut ; les tests dédiés les réactivent avec de petits modèles
   entraînés à la volée. Le géocodeur Nominatim est remplacé par un faux (aucun appel réseau).
2. **Tests d'intrusion** (`tests/test_intrusion.py`) : chaque test joue le rôle d'un attaquant.
3. **Analyse statique** du code de l'application et des scripts avec **Bandit**.
4. **Audit des dépendances** avec **pip-audit** (base de vulnérabilités publique PyPI / OSV),
   y compris les dépendances indirectes.
5. **Test de performance** sur le jeu simulé volumineux (5 000 clients) :
   `scripts/mesurer_performances.py`.
6. **Recette manuelle** des écrans (livreur et administrateur) après chaque étape, puis de nouveau
   après la mise à jour des bibliothèques.

## 3. Tests fonctionnels automatisés

| Domaine | Fichiers | Tests |
|---|---|---|
| Import et nettoyage des données, sectorisation, jeu simulé | `test_nettoyage`, `test_sectorisation`, `test_jeu_simule` | 31 |
| Authentification (bcrypt, JWT, 2FA, verrouillage, limitation de débit) | `test_securite`, `test_auth_api` | 22 |
| Optimisation (matrice, CVRPTW, segmentation hayon / normal) | `test_optim`, `test_cvrptw`, `test_segmentation` | 28 |
| Tournée du livreur, ajout à la volée, recalcul du restant | `test_tournees_api`, `test_ajout_volee` | 10 |
| IA : variables, temps de service, réinjection, anomalies | `test_variables`, `test_temps_service`, `test_temps_predits`, `test_anomalies` | 28 |
| Espace administrateur, RGPD, tableau de bord | `test_admin_comptes`, `test_admin_tournees` | 16 |
| Tests d'intrusion | `test_intrusion` | 11 |
| **Total** | 17 fichiers | **146** |

Exemples de règles métier vérifiées : limite d'effectif de 10 à 20 clients (applicatif et trigger
PostgreSQL), refus d'un client palette dans un camion normal, un livreur ne voit que sa tournée
(réponse 404 identique à une ressource inexistante), pointage Démarrer / Terminer et temps de
service calculé par la base, coupure immédiate des sessions d'un compte désactivé.

## 4. Tests de sécurité

### 4.1 Scénarios d'attaque (tous refusés)

| Tentative | Résultat obtenu |
|---|---|
| Jeton de session signé avec une autre clé | 401, accès refusé |
| Jeton sans signature (`alg: none`) | 401 |
| Jeton authentique de livreur prétendant être « admin » | 403 + trace `ACCES_REFUSE` : le rôle est relu en base |
| Jeton d'un compte inexistant, jeton expiré, en-tête mal formé | 401 |
| Injection SQL dans l'identifiant de connexion, la recherche, l'URL | 401 / 422 / liste vide ; tables intactes |
| Assignation de masse : un livreur glisse l'identifiant d'un client existant | champ ignoré, nouveau client créé, aucun accès aux données de l'autre |
| Fichier ou champs trop volumineux (import de plus de 2 Mo, identifiant trop long) | 422 |
| Lecture directe de la base (copie volée, accès SQL) | digicode et secret 2FA chiffrés (Fernet), mot de passe haché (bcrypt, coût 12) |
| Modification ou suppression d'une trace du journal en SQL direct | refusée par un trigger PostgreSQL |
| Accès aux digicodes d'une tournée | tracé (`CONSULTATION_TOURNEE`), une fois par heure et par tournée |
| Affichage dans une iframe, cache navigateur, appel depuis un site tiers | en-têtes `X-Frame-Options: DENY`, `nosniff`, `Cache-Control: no-store` ; CORS fermé |

S'y ajoutent les tests existants : verrouillage après 5 échecs, message d'erreur identique pour un
compte inconnu ou un mauvais mot de passe, rejeu d'un code TOTP refusé, code de secours à usage
unique, jeton de pré-authentification inutilisable comme session, limitation de débit (429).

### 4.2 Analyse statique (Bandit)

| Constat | Traitement |
|---|---|
| 12 × B311 : générateur `random` non cryptographique | Justifié : uniquement des tirages de simulation reproductibles (graine fixe). Les secrets (mots de passe, codes de secours) utilisent le module `secrets`. Annotation `# nosec B311` avec la justification sur chaque ligne. |
| 1 × B110 : erreur ignorée (`except: pass`) si le modèle d'IA est inutilisable | **Corrigé** : l'erreur est écrite dans les logs ; l'optimisation continue avec les valeurs fixes. |
| Après correction | **0 problème** ; 15 exclusions toutes justifiées dans le code (12 × B311, appels `pip` et URL locale fixes dans deux scripts d'outillage). |

### 4.3 Dépendances (pip-audit)

| Bibliothèque | Avant | Après | Usage dans l'application |
|---|---|---|---|
| FastAPI / Starlette | 0.115.6 / 0.41.3 (7 vulnérabilités) | 0.142.2 / 1.7.0 | serveur de l'API |
| cryptography | 44.0.0 (7) | 50.0.2 | chiffrement Fernet des données sensibles |
| PyJWT | 2.10.1 (15) | 2.15.1 | jetons de session |
| pytest / setuptools | 8.3.4 / 79.0.1 | 9.1.1 / à jour | outillage |
| Streamlit | 1.40.2 (2) | 1.64.0 | interface |
| requests | 2.32.3 (2) | 2.34.2 | appels de l'interface vers l'API |
| Pillow | 11.3.0 (18) | 12.3.0 | génération du QR code 2FA |
| **protobuf** | 5.26.1 (2) | **5.26.1 — risque accepté** | dépendance interne d'OR-Tools |

Les versions ont été choisies par un calcul de compatibilité de pip (`scripts/resoudre_versions.py`,
sans installation), puis validées par les 146 tests et la recette des écrans.

**Risque résiduel accepté — protobuf 5.26.1.** OR-Tools 9.11 impose cette version. Les deux
vulnérabilités (déni de service) ne concernent que l'analyse de messages protobuf **fournis par un
tiers** ; l'application n'en reçoit jamais : protobuf ne sert qu'aux échanges internes du solveur.
Mesure prévue : passer à une version d'OR-Tools compatible avec protobuf corrigé lors d'une prochaine
évolution, avec une nouvelle campagne de tests du solveur.

## 5. Performance (jeu simulé de 5 000 clients)

**Une tournée, un camion normal, créneaux et temps de service prédits**

| Clients | Limite 1 s | Limite 2 s | Limite 5 s | Retards | Non servis |
|---|---|---|---|---|---|
| 10 | 49,1 km | 49,1 km | 49,1 km | 0 | 0 |
| 15 | 74,3 km | 74,3 km | 74,3 km | 0 | 0 |
| 20 | 91,6 km | 91,6 km | 91,6 km | 0 | 0 |

La solution n'évolue plus après 1 s : la limite de calcul (5 s) ne sert qu'à arrêter la recherche.

**Journée complète, zone Lyon - Villeurbanne, 20 % de palettes, flotte de 10 camions normaux et 4 hayons**

| Clients | Tournées | Calcul | Servis | Retards | Camion inadapté | Limite d'effectif dépassée |
|---|---|---|---|---|---|---|
| 60 | 8 | 9,26 s | 60 / 60 | 1 | 0 | 0 |
| 120 | 12 | 9,27 s | 120 / 120 | 0 | 0 | 0 |
| 200 | 18 | 9,36 s | 200 / 200 | 1 | 0 | 0 |

**Autres mesures**

| Mesure | Résultat |
|---|---|
| Temps de service prédit pour les 5 000 clients (Random Forest) | 0,33 s |
| Recherche de clients (« RUE », 500 résultats) | 28 ms |
| Tableau de bord sur 90 jours (43 tournées, 504 livraisons) | 42 ms |
| API `/health` (base comprise), 50 appels | médiane 3,7 ms, 95e centile 6,1 ms |

## 6. Recette manuelle des écrans

| Écran | Vérifications | Résultat |
|---|---|---|
| Connexion | mot de passe puis code 2FA, déconnexion, expiration de session | OK |
| Tournée du livreur | liste, carte, « Optimiser » (57,0 km au lieu de 67,3 km, retour prévu 18:10), alertes de créneaux | OK après mise à jour |
| Ajout d'adresse à la volée | refus camion inadapté / limite atteinte, recalcul du restant | OK (validé lors de l'étape correspondante) |
| Administrateur — tableau de bord | indicateurs, courbe par jour, tournées | OK |
| Administrateur — tournées, livreurs, clients, sécurité | attribution, suivi sur carte, création / désactivation de compte, repositionnement d'un client, journal | OK |

## 7. Anomalies relevées pendant la campagne

| Anomalie | Correction |
|---|---|
| Erreur du modèle d'IA ignorée sans trace (Bandit B110) | écriture dans les logs techniques |
| Consultation des digicodes non tracée | événement `CONSULTATION_TOURNEE`, au plus une fois par heure et par tournée |
| 55 vulnérabilités connues dans les dépendances | mise à jour de 8 bibliothèques ; 1 risque accepté et documenté |
| Option d'affichage `use_container_width` retirée par Streamlit 1.64 | remplacée par `width="stretch"` (27 occurrences) |
| Test du tableau de bord trop dépendant des données de démonstration | attendu recalculé à partir de l'état initial de la base |

## 8. Risques résiduels et recommandations

| Point | Recommandation |
|---|---|
| protobuf 5.26.1 (via OR-Tools) | mise à jour d'OR-Tools lors d'une prochaine version, avec tests du solveur |
| Limiteur de débit en mémoire | à déplacer dans PostgreSQL ou Redis si l'API tourne sur plusieurs processus |
| Pas de HTTPS en local (ports limités à 127.0.0.1) | en production : proxy inverse avec TLS devant l'interface |
| Pas de changement de mot de passe par l'utilisateur lui-même | à ajouter (aujourd'hui : réinitialisation par l'administrateur) |
| Avertissement de Starlette sur le client de test (`httpx`) | sans effet ; à traiter lors d'une prochaine mise à jour |
| Limite de calcul de 5 s pour une tournée | pourrait être ramenée à 2 s sans perte de qualité (mesure §5) |
| Sauvegardes de la base | à mettre en place (`pg_dump` planifié, copie hors du poste) |
