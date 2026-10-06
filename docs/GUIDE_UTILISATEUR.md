# Guide utilisateur — Road Optim, tournées de livraison

Deux profils utilisent l'application : le **livreur** (sur son smartphone) et le **planificateur**
(administrateur, sur un ordinateur). L'adresse de l'application est communiquée par le service
informatique.

## 1. Se connecter (tous les utilisateurs)

**Première connexion**
1. Saisir l'identifiant et, dans le champ « Mot de passe », le **code d'activation** (XXXX-XXXX)
   remis en main propre par le planificateur. Il est valable 72 heures et ne sert qu'une fois.
2. Choisir son mot de passe et le saisir une seconde fois pour confirmation : au moins 12 caractères,
   minuscules, majuscules et chiffre, sans son identifiant. Personne d'autre ne le connaît.
3. Installer sur son téléphone une application d'authentification (Google Authenticator ou
   Microsoft Authenticator), puis scanner le QR code affiché. Si le scan est impossible, saisir la
   clé indiquée sous « Impossible de scanner ? ».
4. Saisir le code à 6 chiffres affiché par l'application.
5. Noter les **10 codes de secours** sur papier ou dans un gestionnaire de mots de passe, hors du
   téléphone : ils ne seront plus jamais affichés. Chacun remplace une fois le code de l'application
   si le téléphone est perdu.

**Connexions suivantes** : identifiant, mot de passe, puis le code à 6 chiffres du moment.

**À savoir**
- La session dure **30 minutes** : il faut ensuite se reconnecter (le temps restant est affiché à
  gauche). Le bouton « Se déconnecter » ferme la session immédiatement.
- Après **5 erreurs** de mot de passe, le compte est bloqué 15 minutes (ou débloqué par le planificateur).
- Un code à 6 chiffres ne sert qu'une fois : en cas de refus, attendre le code suivant (30 secondes).
- Téléphone perdu : se connecter avec un code de secours, puis prévenir le planificateur.
- Mot de passe oublié : le planificateur le réinitialise et remet un nouveau code d'activation ;
  on choisit alors un nouveau mot de passe (la double authentification déjà activée est conservée).

## 2. Livreur : ma tournée du jour

**Mes tournées passées et à venir** (menu) : calendrier des tournées des 60 derniers et 30 prochains
jours, ou d'une date choisie. Une tournée d'un autre jour s'ouvre en consultation seule : carte,
clients et résultats, sans digicode, téléphone ni notes (affichés seulement le jour même).

**L'écran** affiche la date, le camion, la zone et la période de la tournée, le nombre de livraisons
faites, la distance et l'heure de retour prévue, une carte et la liste des clients.

**1. Optimiser.** Au départ, la liste est dans l'ordre d'attribution. Appuyer sur **Optimiser** :
l'application calcule en quelques secondes l'ordre de passage qui respecte les créneaux des clients,
le type de camion et la limite de clients de la zone, en tenant compte du temps estimé chez chaque
client. Sur la carte, le tracé passe du gris pointillé au bleu et les numéros suivent le nouvel ordre.
Un message signale les créneaux qui risquent d'être dépassés et les clients **à reporter**
(impossibles à servir dans cette tournée : le planificateur est prévenu par la liste « À reporter »).

**2. Chez chaque client.** La fiche indique l'adresse, le créneau, l'heure de passage prévue, le
temps sur place estimé, le **digicode**, l'étage et l'ascenseur ; les notes et le téléphone sont dans
« Notes de livraison et contact ».
- En arrivant : **Démarrer la livraison**.
- En repartant : choisir le résultat (**Livré**, **Absent** ou **Refusé**) puis **Terminer la livraison**.
- Une seule livraison peut être en cours à la fois. Les livraisons terminées passent dans « Terminées ».

Couleurs des pastilles sur la carte : bleu à livrer, orange en cours, vert livré, rouge absent ou
refusé, violet à reporter.

**3. Ajouter une adresse en cours de tournée.** Ouvrir « Ajouter une adresse », saisir le nom, le
numéro et la rue, le code postal, la ville, le type de livraison (colis ou palette), éventuellement
le créneau, le digicode, le téléphone et les notes, puis **Ajouter à ma tournée**. L'ajout est refusé
si le client demande un autre type de camion ou si la tournée a atteint la limite de clients de la
zone : le client doit alors être reporté sur une autre tournée. Appuyer ensuite sur **Recalculer
l'itinéraire restant** : seul le reste de la tournée est réorganisé, depuis le dernier client servi.

**Pourquoi pointer ?** Les heures de début et de fin servent uniquement à mieux estimer les temps de
livraison des prochaines tournées, **pas à évaluer la performance individuelle**. Aucune position
GPS n'est enregistrée.

## 3. Planificateur : espace administrateur

Cinq rubriques en haut de l'écran : Tournées, Planning, Livreurs, Clients, Tableau de bord et sécurité.

**Tournées** (le travail quotidien)
1. « Importer un fichier de clients (CSV) » : format de l'échantillon (`id_client`, `nom_client`,
   `adresse`, `creneau_horaire_prefere` obligatoires ; `telephone`, `digicode`, `notes_livraison`
   facultatifs ; séparateur « , » ou « ; »), puis **Importer et préparer les tournées**. Un client
   présent plusieurs fois n'est livré qu'une fois. Le bilan indique les lignes rejetées, les clients non
   placés (adresse non localisée, déjà dans une tournée L) et les tournées préparées ; un bouton géocode
   les adresses en attente et prépare leurs tournées.
2. Les tournées préparées sont listées par code **L1, L2, L3…** (filtres : période, camion, code postal).
   Elles sont **réalisables** : au plus 2 clients prévus dans une même heure, limite selon la période
   (matin 14, après-midi 12, soirée 10 ; 20 seulement sur la journée complète, sans dépasser la limite
   de la zone), ordre horaire, heure de départ du dépôt au plus tard.
3. Ouvrir une tournée L : tableau des clients (passage visé, créneau, client, code postal, ville).
   Choisir la **date** et le **livreur** (libre ce jour-là, bon camion) ; si besoin modifier la
   **période**, l'**heure de départ du dépôt** ou les **clients** (colonne « Action » : retirer, ou
   déplacer vers une autre tournée L). Puis **Attribuer** : la tournée apparaît dans l'application du
   livreur, qui l'optimise (ordre géographique). « Supprimer » retire une tournée L inutile.

**Planning** : grille des livreurs actifs (lignes) et des 7 jours de la semaine (colonnes). Chaque case
montre la tournée du livreur (période, clients ; prévue, en cours ou terminée), sa journée de trajets ou
« libre ». Choisir une tournée L en haut, puis cliquer sur « Attribuer » le jour voulu sur la ligne d'un
livreur libre (période et départ par défaut). Cliquer sur une tournée l'ouvre en dessous : carte,
clients, pointages en direct, « Donner la tournée à » un autre livreur, « Annuler l'attribution » (ses
clients reviennent dans une tournée L), ajout d'un client déjà enregistré.

**Livreurs**
- Créer un compte : un **code d'activation** est affiché dans un encadré jusqu'au clic sur « J'ai remis
  le code au salarié » ; le remettre en main propre ou par téléphone (jamais par e-mail ni en capture
  d'écran). Valable 72 heures, à usage unique. Le salarié choisit ensuite lui-même son mot de passe,
  puis active la double authentification. La colonne « Activation » indique si le code a été utilisé.
- Gérer un compte (cocher la confirmation pour les actions sensibles) : désactiver (effet immédiat,
  sessions coupées), réactiver, déverrouiller, réinitialiser le mot de passe, réinitialiser la double
  authentification (téléphone perdu), affecter un camion.

**Clients**
- « Adresses à vérifier » : clients localisés seulement au centre de leur commune. Cliquer sur la
  carte à l'emplacement exact (ou saisir latitude et longitude relevées sur une carte en ligne), vérifier
  le type de livraison, puis **Enregistrer la correction**.
- Recherche par identifiant, nom ou adresse, puis droits RGPD :
  - **Export** des données d'un client (fichier JSON) : à remettre uniquement à la personne
    concernée, puis à supprimer ;
  - **Anonymisation** (droit à l'effacement), irréversible : ressaisir l'identifiant pour confirmer.
    Impossible si le client a une livraison prévue.

**Tableau de bord et sécurité** — Tableau de bord (7, 30 ou 90 derniers jours) : tournées, livraisons,
passages dans le créneau, remplissage, écart entre temps sur place estimé et réel, alertes. Sécurité :
alertes de connexions anormales détectées par l'IA (heure inhabituelle, nouvel
appareil, échecs répétés…) — elles ne bloquent pas la connexion : vérifier auprès de la personne
concernée — et journal de sécurité filtrable (connexions, refus d'accès, actions d'administration,
consultations des digicodes, exports et anonymisations). Le journal ne peut être ni modifié ni effacé.

## 4. Bonnes pratiques

- Ne jamais communiquer son mot de passe ni ses codes de secours, y compris au planificateur.
- Se déconnecter sur un appareil partagé.
- Ne pas faire de capture d'écran d'un digicode, d'un code d'activation ou d'une fiche client.
- Signaler au planificateur toute alerte de connexion que l'on ne reconnaît pas.
