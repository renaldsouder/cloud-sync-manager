# Installation et exploitation sous Unraid

Documentation de support (UNRAID-004, AIDE-001, AIDE-002).

---

## 1. Installer

Depuis **Apps**, cherchez `Cloud Sync Manager`, puis **Install**. Les valeurs
proposées conviennent à une installation standard.

| Réglage | Valeur | Rôle |
|---|---|---|
| WebUI | `3572` | Port de l'interface |
| Appdata | `/mnt/user/appdata/cloud-sync-manager` → `/config` | Configuration, base, journaux, `rclone.conf` |
| Shares Unraid | `/mnt/user` → `/mnt/user` (rw) | Dossiers à synchroniser |
| PUID / PGID | `99` / `100` | `nobody:users` |
| UMASK | `000` | Fichiers accessibles depuis SMB |
| TZ | votre fuseau | Les heures de planification s'y réfèrent |

Le chemin des shares est **identique des deux côtés** du montage : ce que
l'application affiche est exactement ce que vous voyez dans Unraid.

**Pour restreindre l'accès**, remplacez le mappage `/mnt/user` par un ou
plusieurs shares précis, et alignez `CSM_ALLOWED_ROOTS` sur les mêmes chemins.
L'application refusera alors toute tâche pointant ailleurs.

Quoi qu'il arrive, les shares `appdata`, `system` et `domains` ne peuvent
jamais être la **destination** d'une synchronisation.

---

## 2. Ajouter un stockage Cloud

Onglet **Stockages Cloud** → **Ajouter un stockage**.

### S3, Backblaze B2, WebDAV, SFTP

Authentification par clé ou mot de passe : tout se saisit dans l'interface.
Rien d'autre à faire.

### Google Drive, OneDrive, Dropbox

Ces trois fournisseurs exigent une autorisation par navigateur. Elle se
conduit depuis l'interface, sans installer quoi que ce soit :

1. Choisissez le fournisseur, puis **Autoriser l'accès**.
2. Un onglet s'ouvre sur la page de connexion. Identifiez-vous et acceptez.
3. Votre navigateur atterrit sur une **page d'erreur de connexion** — c'est
   normal : le fournisseur renvoie vers `localhost`, c'est-à-dire votre poste,
   et non le serveur.
4. Copiez l'adresse complète de cette page et collez-la dans le champ prévu,
   puis **Terminer l'autorisation**.

Le jeton est rempli automatiquement. Pour OneDrive, `drive_id` et `drive_type`
le sont aussi — l'application les demande à Microsoft pour vous.

Ce parcours a besoin que le **port 53682** soit publié par le conteneur ; le
template le prévoit. Il n'est utilisé que le temps d'une autorisation.

Le jeton est écrit directement dans le `rclone.conf` du conteneur. Il n'est
jamais réaffiché ensuite, ni inclus dans une sauvegarde de configuration.

**Testez toujours le stockage** après création : le test relève ce qui répond
réellement. Une connexion réussie ne garantit pas que toutes les opérations
sont supportées par le fournisseur.

---

## 3. Comprendre les modes

C'est le point le plus important de cette documentation.

### Copie

Envoie les fichiers nouveaux et modifiés vers la destination. **Ne supprime
jamais rien** à destination. Un fichier effacé à la source reste en place de
l'autre côté.

C'est le mode à choisir en cas de doute.

### Miroir

La destination devient le reflet exact de la source. **Ce qui n'existe plus à
la source est retiré de la destination.**

Ce mode est protégé par plusieurs garde-fous, dans cet ordre :

1. **Simulation obligatoire** avant la première exécution, et de nouveau après
   tout changement de source, de destination ou de sens.
2. **Contrôle de la source** : si elle est inaccessible, ou vide alors que la
   destination ne l'est pas, la tâche échoue sans rien supprimer. C'est la
   protection contre le share démonté.
3. **Seuil de suppression** : au-delà du nombre ou du pourcentage configuré,
   la tâche passe en *Bloquée* et attend une validation qui nomme le nombre
   exact de fichiers et affiche leur liste.
4. **Corbeille** : les suppressions sont des déplacements vers
   `.cloudsync-trash` à destination, purgée selon la rétention configurée.

### Bidirectionnel

Non disponible. Il ne sera proposé qu'après validation complète de sa matrice
de tests — livré à moitié fiable, il ferait perdre des données.

---

## 4. Sens de synchronisation

- **Local → Cloud** : vos fichiers partent vers le Cloud. La destination est
  distante ; le local n'est jamais modifié.
- **Cloud → Local** : les fichiers descendent dans le share choisi. La
  destination est **votre serveur**. En Miroir, les seuils par défaut y sont
  volontairement plus stricts.

---

## 5. Planification

Aucune expression cron à écrire : intervalle régulier, heure fixe quotidienne,
ou jours de la semaine choisis. Les heures suivent le fuseau `TZ` du conteneur.

**Rattrapage.** Si le serveur était éteint au moment d'une échéance, rien n'est
rejoué par défaut : la tâche repart à l'occurrence suivante. Vous pouvez
demander un rattrapage — il rejoue **une seule** occurrence, jamais toutes
celles qui ont été sautées, ce qui lancerait plusieurs synchronisations
concurrentes de la même tâche.

Une exécution qui déborde sur son échéance suivante fait sauter le tour au
lieu de s'empiler.

---

## 6. Filtres

Les règles sont **ordonnées** et la première qui correspond l'emporte.

Dès qu'une règle d'inclusion existe, tout ce qui n'est pas explicitement inclus
est écarté — sans quoi « n'inclure que les photos » ne filtrerait rien.

L'outil de test montre, pour un dossier donné, ce qui serait retenu et ce qui
serait écarté, avec la règle responsable. Utilisez-le avant la première
exécution réelle.

---

## 7. Notifications

Deux canaux, tous deux optionnels, dans **Paramètres**.

- **Unraid** : renseignez l'adresse de votre serveur et une clé d'API.
  Elle n'est jamais réaffichée ni exportée.

  Indiquez bien l'adresse du serveur sur le réseau — `https://192.168.x.x` —
  et non `localhost` : en réseau *bridge*, `localhost` désigne le conteneur
  lui-même, y compris lorsque l'application tourne sur le serveur qu'elle
  notifie.

  **Créez une clé limitée aux notifications, pas une clé `admin`.** Notre
  besoin se réduit à écrire un message ; un rôle `admin` donnerait en prime
  l'accès à votre baie, vos disques, vos VM, votre Docker et votre réseau.

  Le plus simple : **Settings → Management Access → API Keys → Create from
  Template**, et collez cette chaîne, qui pré-remplit le formulaire avec la
  seule permission nécessaire.

  ```
  ?name=Cloud%20Sync%20Manager&description=Notifications%20de%20synchronisation&scopes=notifications:create
  ```

  Si cette forme est refusée, essayez `scopes=NOTIFICATIONS:CREATE_ANY` :
  l'interface accepte l'action courte, la ligne de commande la forme longue.

  En ligne de commande, l'équivalent est :

  ```
  unraid-api apikey --create --name "Cloud Sync Manager" --permissions "NOTIFICATIONS:CREATE_ANY"
  ```

  Sans modèle, créez la clé à la main en choisissant les permissions
  personnalisées plutôt qu'un rôle, puis `NOTIFICATIONS` seul. L'API est
  intégrée à Unraid depuis la 7.2.

  **Si votre serveur est en `https://`**, cochez « accepter un certificat
  auto-signé » : Unraid présente par défaut un certificat que rien ne peut
  valider, et la connexion serait refusée avant même d'être tentée. Le
  trafic reste chiffré ; c'est l'identité du serveur qui n'est plus
  vérifiée, ce qui est sans conséquence vers votre propre machine sur votre
  propre réseau. Une adresse en `http://` évite la question.

  Si le test échoue avec cette seule permission, ajoutez `READ_ANY` avant
  d'élargir davantage : la mutation utilisée déduplique les notifications et
  a peut-être besoin de relire les existantes.
- **Webhook** : une URL qui recevra un POST JSON — Home Assistant, Discord,
  n'importe quoi d'autre.

Par défaut, seuls les événements demandant une action sont signalés : échec,
tâche bloquée, authentification expirée. Une notification qui n'aboutit pas ne
fait jamais échouer une synchronisation.

---

## 8. Sauvegarde de la configuration

**Paramètres → Exporter** produit un fichier JSON contenant vos stockages,
tâches, filtres et réglages.

Ce fichier **ne contient aucun identifiant Cloud**. C'est délibéré : une
sauvegarde finit sur une clé USB ou dans un dépôt Git, et y disséminer des
jetons serait pire que l'inconvénient de les ressaisir.

À la restauration, les tâches importées arrivent **en pause**. Vérifiez les
chemins et réauthentifiez les stockages avant de les réactiver — un serveur de
destination dont les shares diffèrent ne doit pas déclencher une
synchronisation destructive au premier battement.

Avant toute mise à jour majeure, sauvegardez aussi le dossier appdata.

---

## 9. Diagnostic

| Symptôme | Cause probable | Action |
|---|---|---|
| « Moteur rclone indisponible » | Image incomplète | Recréez le conteneur |
| Tâche *Bloquée* | Seuil de suppression dépassé | Ouvrez la tâche : la liste des fichiers concernés est affichée |
| « la source ne contient aucun fichier… » | Share démonté ou chemin disparu | Vérifiez le montage avant de relancer |
| Fichiers inaccessibles depuis SMB | `PUID`/`PGID`/`UMASK` | Remettez `99` / `100` / `000` |
| Authentification expirée | Jeton OAuth révoqué | Rejouez `rclone authorize` et recollez le jeton |
| « certificat non vérifiable » | Unraid en HTTPS avec certificat auto-signé | Cochez « accepter un certificat auto-signé », ou passez l'adresse en `http://` |
| « clé d'API refusée » | Permissions insuffisantes | Ajoutez `NOTIFICATIONS:READ_ANY` à la clé |
| Heures de planification décalées | `TZ` absent | Renseignez votre fuseau |

Chaque exécution conserve son résultat, son code de retour, la version de
rclone utilisée et la liste des fichiers transférés, ignorés, supprimés ou en
erreur. Le jeu de filtres appliqué est conservé dans
`/config/filters/<id>.filter`.

Aucun secret n'apparaît dans les journaux : ils passent tous par un filtre de
masquage centralisé.

---

## 10. Protéger l'interface

À l'installation, **l'interface n'est protégée par aucun mot de passe** :
toute personne pouvant joindre le port sur votre réseau peut créer une tâche
et déclencher des suppressions. C'est le défaut retenu pour ne pas verrouiller
les installations existantes lors d'une mise à jour, mais l'application vous le
signale tant que ce n'est pas fait.

**Paramètres → Accès à l'interface**, choisissez un mot de passe d'au moins
huit caractères. Vous restez connecté ensuite ; cochez « rester connecté » pour
trente jours sur ce navigateur.

Le mot de passe est conservé sous forme d'empreinte `scrypt` salée — il n'est
stocké nulle part en clair et ne peut pas être relu. Les tentatives répétées
depuis une même origine sont ralenties après cinq échecs.

**Mot de passe perdu ?** Depuis l'appdata du conteneur, supprimez le fichier
`session.key` et l'entrée `auth.password_hash` de la base : l'interface
redevient ouverte, et vous pouvez en définir un nouveau.

### Accès depuis l'extérieur

N'ouvrez **jamais** ce port directement depuis votre box. L'application parle
en HTTP simple, sans chiffrement : le mot de passe circulerait en clair.

Pour un accès distant, placez-la derrière un reverse proxy HTTPS — SWAG ou
Nginx Proxy Manager — qui portera le certificat. Le mot de passe de
l'application vient alors en complément du chiffrement, pas à sa place.
