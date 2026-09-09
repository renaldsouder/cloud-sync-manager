# CLAUDE.md — Cloud Sync Manager (Unraid)

> **Source de vérité produit** : [`docs/Cloud_Sync_Manager_Cahier_des_charges.md`](docs/Cloud_Sync_Manager_Cahier_des_charges.md)
> Ce fichier-ci est un **résumé opérationnel**. En cas de divergence, le cahier des charges fait foi (§29).

## Avant toute modification significative

Lire le chapitre concerné du cahier des charges. Ne lire que les fichiers utiles à la tâche.

## Règles non négociables (extraits — voir §8, §18, §28)

- **Fail closed** : source inaccessible, vide anormalement ou listing non fiable ⇒ **échec sans propager de suppression**.
- **Simulation avant destruction** : dry-run obligatoire à la 1re exécution d'un Miroir, après changement de source/destination/sens, après activation de la propagation des suppressions, avant init bidirectionnelle.
- **Seuil de suppression** configurable (nombre et/ou %) ⇒ dépassement = statut `Bloqué — validation nécessaire`.
- **Suppression distante jamais activée par défaut.**
- Après arrêt forcé ou crash : `Interrupted`, **jamais** `Success`.
- **Secrets** : jamais en clair dans les logs, l'API de lecture, un export ou un package de diagnostic. UI = « configuré ».
- **rclone** : toujours `subprocess` avec liste d'arguments, **jamais** `shell=True` ni concaténation shell.
- **Chemins** : validation stricte, interdiction de `..` et de toute évasion hors des racines montées.
- Jamais `--privileged`, jamais `/var/run/docker.sock`.
- Ne pas introduire sans décision explicite : Docker Compose comme prérequis, PostgreSQL/MySQL, Redis, Celery, Kubernetes, télémétrie externe.
- **Aucune optimisation de confort, de vitesse ou de simplicité ne justifie de réduire les protections contre la perte de données.**

## Vocabulaire produit (§7) — contrat UI

- **Copie** = `rclone copy` — pas de suppression à destination.
- **Miroir** = `rclone sync` — destructif, protections §8 obligatoires.
- **Bidirectionnel** = `rclone bisync` — hors MVP, conditionné à la validation de sécurité.
- Ne jamais appeler « sauvegarde » ce qui n'offre pas les garanties d'une sauvegarde versionnée.

## Stack (§5.2)

Backend Python 3.13 + FastAPI · Frontend React + TypeScript + Vite · SQLite · rclone en processus enfant contrôlé · SSE pour la progression · planificateur interne persistant · image Docker multi-stage, **un seul conteneur**.

## Backlog

Identifiants stables (`SYNC-001`, `SEC-004`, …) définis au §26. Les utiliser dans les commits, notes de version et comptes rendus. Priorité `P0` → `P1` → `P2` → `P3`.
Statuts : `À faire` · `En cours` · `À tester` · `Validé` · `Disponible` · `Bug` · `Bloqué` · `Demande` · `Reporté`.
Une exigence abandonnée reste tracée avec un statut explicite — jamais supprimée silencieusement.

## Definition of Done (§19)

Code fonctionnel · test unitaire si pertinent · test d'intégration si rclone/SQLite/filesystem · gestion d'erreur · aucune fuite de secret · état UI cohérent · comportement documenté · backlog mis à jour · aucune régression P0/P1.
Une fonction destructive n'est **jamais** terminée sans test automatisé **et** test d'intégration.

## Format de compte rendu (§27)

```text
ID : SYNC-004
Statut : À tester
Version : 0.1.0

Fait :
- ...

Tests :
- ...

Risques / limites :
- ...

Fichiers modifiés :
- ...
```

## Suivi de projet — outil externe (§29)

Le suivi projet vit **hors du dépôt applicatif**, dans `../_Suivi_Projet/` :

- `Suivi_Projet_Local.html` — outil local mono-fichier (sql.js + File System Access API) : modules, entrées, cahier des charges, pièces jointes.
- `cloudsyncmanage.sqlite` — **base du suivi projet**, source de vérité (18 modules, 97 entrées).
- Il exporte `*_cahier_des_charges.json` (le « JSON de suivi » du §29), un `.zip` avec pièces jointes, la `.sqlite`, et le Markdown du cahier des charges.

> ⚠️ **`docs/Cloud_Sync_Manager_Cahier_des_charges.md` est un export généré**, pas un fichier à éditer à la main : toute modification serait écrasée au prochain export. Les corrections se font dans l'outil de suivi, puis on ré-exporte.

**Rien à voir avec le SQLite du §6**, qui est la base applicative de Cloud Sync Manager (`remotes`, `tasks`, `task_runs`, `task_events`, `filter_sets`, `settings`) stockée dans l'appdata Unraid. Deux bases distinctes, deux cycles de vie distincts.

### Écart d'export constaté le 09/09/2026

Le `.md` (08/09 19:24) est antérieur à la `.sqlite` (08/09 19:46) : 4 entrées ont été durcies dans l'outil après l'export. **La base fait foi**, le `.md` est simplement à ré-exporter.

| ID | `.md` (export périmé) | `.sqlite` (à jour, fait foi) |
|---|---|---|
| SYNC-003 | `À faire` / `V1.0` — bidirectionnel avec gestion explicite des conflits | `Demande` / `V1 conditionnelle` — bisync **uniquement après validation complète** des tests de sécurité et de conflits |
| SYNC-004 | dry-run « indiquant » créations/màj/suppressions | dry-run **obligatoire** avant toute première exécution destructive |
| CONF-002 | non destructif par défaut **ou** choix explicite | non destructif par défaut **et** validation explicite exigée |
| SEC-004 | privilèges minimaux, éviter `privileged` | privilèges minimaux, **sans** `privileged` **ni** accès au Docker socket |

## Commandes

Backend (venv dans `backend/.venv`, Python ≥ 3.13) :

```bash
cd backend && .venv/Scripts/python -m pytest -q
```

```bash
cd backend && CSM_CONFIG_DIR=./dev-appdata .venv/Scripts/python -m uvicorn csm.main:app --reload --port 3572
```

Frontend : `cd frontend && npm install && npm run dev` (proxy `/api` → 3572) ou `npm run build`.

Image : `docker build -f docker/Dockerfile -t cloud-sync-manager:0.1.0 .` (contexte = racine du dépôt).

Nouvelle migration : `cd backend && .venv/Scripts/alembic revision --autogenerate -m "..."` — **et son test** dans `tests/test_migrations.py` (§27.7).

## Structure

```
backend/src/csm/  config, paths (LOCAL-005), security/redaction (SEC-002),
                  db/ (modèle §6 + Alembic), rclone/, api/, main.py
frontend/src/     WebUI React + TypeScript
docker/           Dockerfile multi-stage, entrypoint PUID/PGID/UMASK
```
