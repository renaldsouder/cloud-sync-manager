<p align="center">
  <img src="unraid/images/cloud-sync-manager.png" width="120" alt="Cloud Sync Manager">
</p>

<h1 align="center">Cloud Sync Manager</h1>

<p align="center">
  Synchronisation Cloud pour Unraid — un stockage, un dossier, un sens, une
  planification, et ça tourne. Sans ligne de commande.
</p>

<p align="center">
  <a href="README.md">🇬🇧 English version</a> ·
  <a href="https://rclone.org">Propulsé par rclone</a> ·
  <a href="LICENSE">GPL-3.0-or-later</a>
</p>

---

Unraid stocke très bien les données, mais synchroniser un partage vers un
service Cloud suppose d'assembler des outils, d'apprendre des commandes, ou
d'installer une interface générique qui n'a pas été pensée pour Unraid. Cloud
Sync Manager vise l'expérience de Synology Cloud Sync : on choisit un
fournisseur, un dossier, un sens, et c'est réglé.

Un conteneur. Une interface Web. rclone en dessous.

## Maturité — à lire avant d'installer

Version **0.2.4**. Elle est publiée, elle tourne, et les garde-fous décrits plus
bas sont réels. C'est aussi un projet dont le premier commit date du 9 septembre
2026, et dont le propre critère de réussite vient tout juste de commencer à
courir.

| | |
|---|---|
| Garde-fous destructifs (seuil, source absente, corbeille, simulation) | Tests automatisés **et** d'intégration — mais contre un dossier local tenant lieu de Cloud |
| Éprouvé avec un vrai compte | **OneDrive uniquement** |
| Google Drive, Dropbox | Parcours OAuth implémenté, testé jusqu'à la page de consentement, jamais mené au bout avec un vrai compte |
| S3, B2, WebDAV, SFTP | Implémentés, **jamais confrontés à un vrai service** |
| Notifications Unraid | Validées sur un vrai Unraid 7.3.2 |
| 30 jours d'utilisation réelle — le critère de réussite du projet (§22) | En cours, démarré le 9 septembre 2026 |
| Bidirectionnel | Délibérément absent tant que sa matrice de tests destructifs n'est pas complète |
| Interface en anglais | Pas faite — l'interface est en français uniquement |

Deux conséquences.

**Un dossier local ne sait pas reproduire ce qu'un Cloud fait mal** : listings
non fiables, limitation de débit, cohérence différée. Ce sont exactement les
conditions pour lesquelles les règles de *fail closed* existent, et elles n'ont
pas encore été rencontrées en vrai.

**L'usage réel trouve ce que la suite de tests ne peut pas voir.** Six défauts
sont déjà remontés que 243 tests au vert n'avaient pas attrapés : un port OAuth
qui ne pouvait pas fonctionner, des noms de fichiers accentués corrompus au
décodage, des exécutions affichées « en cours » indéfiniment. Il y en aura
d'autres. Commencez par une **Copie**, sur des données dont vous avez une
sauvegarde — pas par un **Miroir** sur votre seul exemplaire.

## Ce qui fait la différence

Beaucoup d'outils savent copier des fichiers vers un Cloud. La valeur est
ailleurs : dans ce qui se passe quand ça tourne mal.

**Rien de destructif ne se fait en silence.** Une exécution en Miroir mesure ce
qu'elle supprimerait *avant* de toucher à quoi que ce soit. Au-delà d'un seuil
que vous fixez, la tâche s'arrête en `Bloqué` et demande confirmation — et le
bouton nomme l'action réelle, « Supprimer 423 fichiers », jamais
« Confirmer ». Les fichiers concernés sont listés.

**Une source absente ne devient jamais une suppression de masse.** Si la source
est inaccessible, ou vide alors que la destination ne l'est pas, la tâche échoue
sans rien propager. C'est le scénario du partage non monté, celui qui détruit
les données dans les outils qui traduisent « vide » par « supprime tout ».

**Les suppressions sont réversibles.** Elles partent dans un dossier
`.cloudsync-trash` à la destination, purgé selon une rétention que vous
contrôlez. Les versions écrasées y vont aussi.

**Une exécution interrompue n'est jamais annoncée comme réussie.** Arrêtez-la,
ou faites tomber le conteneur en plein transfert : l'exécution est enregistrée
`Interrompue`.

**Simulation avant destruction.** La première exécution d'un Miroir exige un
essai à blanc, et tout changement de source, de destination ou de sens aussi.

**Les identifiants Cloud ne fuitent pas.** Ils vivent dans le fichier de
configuration de rclone, jamais dans la base, jamais dans un argument de ligne
de commande, jamais dans un journal, jamais dans un export de configuration.

## Installation sur Unraid

### Depuis Community Applications

Cherchez `Cloud Sync Manager` dans l'onglet **Apps**.

### Depuis l'URL du modèle

**Docker → Add Container**, puis collez ceci dans le champ *Template* :

```
https://raw.githubusercontent.com/renaldsouder/unraid-templates/main/cloud-sync-manager.xml
```

### À la main

| Réglage | Valeur |
|---|---|
| Repository | `ghcr.io/renaldsouder/cloud-sync-manager:latest` |
| Network | `bridge` |
| Port | `3572` → `3572` |
| Path | `/config` → `/mnt/user/appdata/cloud-sync-manager` |
| Path | `/mnt/user` → `/mnt/user` |
| Variables | `PUID=99`, `PGID=100`, `UMASK=000`, `TZ=Europe/Paris` |

Le chemin des partages est monté **au même endroit des deux côtés**, pour que ce
que montre l'interface soit exactement ce que vous voyez dans Unraid. `appdata`,
`system` et `domains` ne peuvent jamais être une *destination* de
synchronisation, quelle que soit la configuration.

Ouvrez ensuite l'interface et définissez un mot de passe dans **Paramètres →
Accès à l'interface**. Tant que ce n'est pas fait, l'application vous prévient
sur chaque écran que n'importe qui sur votre réseau peut déclencher une
suppression.

## Connecter un stockage Cloud

Deux familles de fournisseurs, deux parcours.

Le tableau de maturité plus haut dit lesquels ont réellement été éprouvés avec
un vrai compte.

### Clé ou mot de passe — S3, Backblaze B2, WebDAV, SFTP

Tout se saisit dans l'interface. Ajoutez le stockage, remplissez les champs,
testez. Rien d'autre à installer.

### Autorisation par navigateur — Google Drive, OneDrive, Dropbox

Ces trois-là exigent un aller-retour OAuth par le navigateur. Il se pilote
depuis l'interface : vous n'installez pas rclone et vous n'ouvrez pas de
terminal.

1. **Stockages Cloud → Ajouter un stockage**, choisissez le fournisseur.
2. Cliquez sur **Autoriser l'accès**. Un onglet s'ouvre sur la page de connexion
   du fournisseur.
3. Connectez-vous et accordez l'accès.
4. Votre navigateur atterrit sur une **page d'erreur de connexion à
   `localhost:53682`**. **C'est normal et attendu.** L'adresse de redirection
   que rclone a enregistrée chez les fournisseurs désigne `localhost`,
   c'est-à-dire *votre* machine et non le serveur — personne n'y répond.
5. **Copiez l'adresse complète de cette page d'erreur** depuis la barre
   d'adresse. Elle ressemble à `http://localhost:53682/?code=…&state=…`.
6. Collez-la dans le champ de l'interface et cliquez sur **Terminer
   l'autorisation**.

Le jeton est renseigné automatiquement. Pour OneDrive, `drive_id` et
`drive_type` le sont aussi — l'application les demande à Microsoft pour vous.

> **Si OneDrive signale un identifiant de disque manquant**, le stockage est
> tout de même créé avec son jeton, et la liste affiche un bouton **Compléter
> la configuration** qui réinterroge Microsoft. Inutile de refaire
> l'autorisation.

Supprimer ce dernier copier-coller supposerait d'enregistrer notre propre
application OAuth chez chaque fournisseur, avec le domaine public et le
processus de validation que cela implique. Un collage reste aujourd'hui le
meilleur compromis disponible.

**Testez toujours un stockage après l'avoir créé.** Une connexion réussie ne
garantit pas que toutes les opérations soient supportées par ce fournisseur.

Le jeton est écrit directement dans le `rclone.conf` du conteneur. Il n'est
jamais réaffiché ensuite, ni inclus dans une sauvegarde de configuration. Le
parcours détaillé, avec les cas d'échec, est dans
[le guide d'installation](docs/Installation_Unraid.md#2-ajouter-un-stockage-cloud).

## Première synchronisation

Commencez par une **Copie** : elle ajoute et met à jour, et ne supprime jamais
rien à la destination. Lancez d'abord la **simulation** — elle liste exactement
ce qui serait transféré, sans rien écrire.

Ne passez au **Miroir** qu'une fois quelques copies passées proprement. Sa
première exécution exigera de toute façon une simulation.

## Configuration

Tout l'état persistant vit sous `/config` : la base SQLite, la configuration
rclone, les journaux et les jeux de filtres appliqués.

| Variable | Défaut | Rôle |
|---|---|---|
| `CSM_CONFIG_DIR` | `/config` | Racine de l'appdata |
| `CSM_PORT` | `3572` | Port de l'interface Web |
| `CSM_ALLOWED_ROOTS` | `/mnt/user` | Racines locales, séparées par des virgules, hors desquelles aucune tâche ne peut sortir |
| `CSM_HISTORY_RETENTION_DAYS` | `90` | Âge au-delà duquel une exécution est purgée |
| `CSM_HISTORY_KEEP_RUNS` | `200` | Exécutions conservées par tâche quel que soit leur âge |
| `CSM_QUARANTINE_RETENTION_DAYS` | `30` | Âge au-delà duquel un lot de corbeille est purgé |
| `CSM_QUARANTINE_KEEP_RUNS` | `3` | Lots de corbeille toujours conservés, quel que soit leur âge |
| `CSM_SCHEDULER_POLL_SECONDS` | `20` | Battement du planificateur |
| `TZ` | — | Fuseau du serveur ; les heures de planification s'y réfèrent |
| `PUID` / `PGID` | `99` / `100` | Propriétaire des fichiers écrits (`nobody:users`) |
| `UMASK` | `000` | Permissions des fichiers écrits |

## Fonctionnalités

- **Sens** — Local → Cloud et Cloud → Local
- **Modes** — Copie (ne supprime jamais) et Miroir (protégé, voir plus haut)
- **Planification** — toutes les N minutes, chaque jour à heure fixe, ou certains
  jours de la semaine. Pas d'expression cron. Politique de rattrapage explicite
  après un redémarrage du serveur.
- **Filtres** — règles ordonnées d'inclusion/exclusion par chemin, extension,
  motif de nom, fichiers cachés et taille, avec un outil d'aperçu qui montre
  quelle règle a tranché
- **Progression en direct** — fichier courant, débit, temps restant, en SSE
- **Historique** — résultat, code de sortie, fichiers transférés et supprimés,
  version de rclone, avec rétention configurable
- **Détail fichier par fichier** — ouvrez une exécution pour voir chaque fichier
  transféré, supprimé, ignoré ou en erreur, avec recherche par chemin. Une liste
  tronquée le dit franchement : ne pas y trouver un fichier ne signifie jamais
  qu'il n'a pas été traité.
- **Notifications** — API intégrée d'Unraid et webhook générique
- **Sauvegarde** — export et restauration de la configuration, identifiants
  volontairement exclus ; les tâches restaurées arrivent en pause
- **Bidirectionnel** — délibérément **indisponible** tant que sa matrice de tests
  destructifs n'est pas complète. À moitié fiable, il perdrait des données.

## Développement

Les tests d'intégration du backend ont besoin du binaire rclone. Placez-le dans
`backend/.tools/` (ignoré par git) ou pointez `CSM_RCLONE_BINARY` dessus ; sinon
ces tests sont ignorés plutôt qu'en échec.

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest
```

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxy /api vers le port 3572
npm run build      # tsc -b && vite build
```

```bash
docker build -f docker/Dockerfile -t cloud-sync-manager .
```

```
backend/    API FastAPI, modèle SQLite, migrations Alembic, adaptateur rclone
frontend/   Interface Web React + TypeScript (Vite)
docker/     Dockerfile multi-étages, entrypoint PUID/PGID
unraid/     Modèle Community Applications, profil de dépôt, icône
docs/       Cahier des charges, décisions techniques, guide d'installation
```

- [Guide d'installation et d'utilisation](docs/Installation_Unraid.md)
- [Cahier des charges](docs/Cloud_Sync_Manager_Cahier_des_charges.md)
- [Propositions techniques](docs/Propositions_Techniques_Unraid.md)

## Licence

**GPL-3.0-or-later** pour l'application ([`LICENSE`](LICENSE)) — une version
dérivée doit rester ouverte, ce qui protège les garde-fous contre la perte de
données plutôt que de laisser quiconque les refermer.

**MIT** pour le dépôt de modèles Unraid ([`unraid/LICENSE`](unraid/LICENSE)),
qui n'est que du XML descriptif.

Les deux sont approuvées OSI, comme l'exige Community Applications. rclone est
sous MIT et n'est pas lié au code — l'application l'exécute comme processus
enfant.
