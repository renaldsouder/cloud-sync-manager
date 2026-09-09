# Cloud Sync Manager

Synchronisation Cloud pour Unraid : choisir un stockage distant, un dossier
local, un sens de synchronisation, planifier, et laisser fonctionner — sans
ligne de commande.

Moteur [rclone](https://rclone.org). Un seul conteneur, une seule WebUI.

> **État : J6 — périmètre V1.** Copie et Miroir dans les deux sens, protections
> destructives complètes, planification sans cron, tableau de bord, filtres avec
> outil de test, limites de transfert, notifications, sauvegarde et restauration
> de configuration, template Community Applications et documentation.
> Le bidirectionnel reste hors périmètre tant que sa matrice de tests n'existe pas.

- Cahier des charges : [`docs/Cloud_Sync_Manager_Cahier_des_charges.md`](docs/Cloud_Sync_Manager_Cahier_des_charges.md)
- Décisions techniques : [`docs/Propositions_Techniques_Unraid.md`](docs/Propositions_Techniques_Unraid.md)
- Installation et exploitation : [`docs/Installation_Unraid.md`](docs/Installation_Unraid.md)
- Règles de développement : [`CLAUDE.md`](CLAUDE.md)

## Structure

```
backend/    API FastAPI, modèle SQLite, migrations Alembic, adaptateur rclone
frontend/   WebUI React + TypeScript (Vite)
docker/     Dockerfile multi-stage et entrypoint PUID/PGID
unraid/     Template Community Applications et profil de dépôt
docs/       Cahier des charges, décisions et documentation d'installation
```

## Développement

### Backend

Les tests d'intégration ont besoin du binaire rclone. Déposez-le dans
`backend/.tools/` (ignoré par git) ou renseignez `CSM_RCLONE_BINARY` ; à défaut,
ces tests sont ignorés plutôt qu'en échec.

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"     # Linux/macOS : .venv/bin/python
.venv/Scripts/python -m pytest
CSM_CONFIG_DIR=./dev-appdata .venv/Scripts/python -m uvicorn csm.main:app --reload --port 3572
```

### Frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxy /api vers le port 3572
npm run build      # produit dist/, servi par l'API en production
```

### Image Docker

```bash
docker build -f docker/Dockerfile -t cloud-sync-manager:0.1.0 .
```

```bash
docker run -d --name cloud-sync-manager -p 3572:3572 -v /mnt/user/appdata/cloud-sync-manager:/config -v /mnt/user:/mnt/user -e PUID=99 -e PGID=100 -e UMASK=000 cloud-sync-manager:0.1.0
```

## Configuration

Tout l'état persistant vit sous `/config` (appdata Unraid) : base SQLite,
`rclone.conf` chiffré, journaux.

| Variable | Défaut | Rôle |
|---|---|---|
| `CSM_CONFIG_DIR` | `/config` | Racine de l'appdata |
| `CSM_PORT` | `3572` | Port de la WebUI |
| `CSM_ALLOWED_ROOTS` | `/mnt/user` | Racines locales autorisées, séparées par des virgules |
| `CSM_WEB_DIR` | `/app/web` | Build du frontend servi par l'API |
| `CSM_HISTORY_RETENTION_DAYS` | `90` | Âge au-delà duquel une exécution est purgée |
| `CSM_HISTORY_KEEP_RUNS` | `200` | Exécutions conservées par tâche, quel que soit leur âge |
| `CSM_SCHEDULER_POLL_SECONDS` | `20` | Battement du planificateur |
| `TZ` | — | Fuseau du serveur ; les heures de planification s'y réfèrent |
| `CSM_QUARANTINE_RETENTION_DAYS` | `30` | Âge au-delà duquel une corbeille est purgée |
| `CSM_QUARANTINE_KEEP_RUNS` | `3` | Corbeilles toujours conservées, quel que soit leur âge |
| `PUID` / `PGID` | `99` / `100` | Identité des fichiers écrits (`nobody:users`) |
| `UMASK` | `000` | Droits des fichiers écrits |

## Sécurité

- Aucun secret Cloud en base : ils vivent dans le `rclone.conf` chiffré.
- Aucun secret en argument de commande — `argv` est lisible via `/proc`.
- Filtre de redaction centralisé avant tout log, export ou diagnostic.
- Ni `--privileged`, ni accès au socket Docker.
- Aucune suppression distante activée par défaut ; simulation obligatoire
  avant la première exécution destructive.
- Source vide ou inaccessible : la tâche échoue **sans propager de suppression**.
- Au-delà du seuil configuré, la tâche passe « Bloquée » et attend une
  validation explicite qui nomme le nombre exact de fichiers concernés.
- Les suppressions sont des déplacements vers `.cloudsync-trash` : réversibles,
  et purgées selon une rétention configurable pour ne pas remplir le share.

## Licence

**À définir.** Une licence approuvée OSI est exigée pour publier dans
Community Applications (UNRAID-005).
