# Propositions techniques — faire tourner Cloud Sync Manager sous Unraid

> **Statut : propositions à arbitrer.** Ce document ne modifie ni le cahier des charges ni la base de suivi.
> Relevé Community Applications du **09/09/2026** (`assets.ca.unraid.net/feed/applicationFeed.json`, 4298 applications).

---

## 0. Ce que dit le catalogue Community Applications

| Constat | Donnée | Conséquence |
|---|---|---|
| **Aucun équivalent Synology Cloud Sync** | `rclone` brut (124 M pulls), `Rclone-mount`, `binhex-rclone`, `Nacho-Rclone-Native-GUI`. Les outils à UI sont des **sauvegardes** (Duplicati 182 M, backrest 4,6 M, Kopia, Duplicacy) | Le créneau « sync Cloud pilotée à la souris » est réellement vide |
| **Nom libre** | 0 application contenant « cloud » + « manager » ; 0 « cloudsync » | **UNRAID-006** : `Cloud Sync Manager` est disponible |
| **Non privilégié = la norme** | 3603 apps `Privileged=false` contre 128 `true` | **SEC-004** est aligné avec l'écosystème, aucun compromis à faire |
| **Registres** | Docker Hub 2298, GHCR 1276, LSCR 239 | GHCR est parfaitement accepté |
| **Catégories utiles** | `Backup:`, `Cloud:`, `Tools:Utilities` | Cible : `Cloud: Backup: Tools:Utilities` |
| **Ports saturés** | 3000 (366 apps), 8080 (364), 8000 (161), 5000 (79) | **Port par défaut proposé : `3572`** — 0 collision dans le catalogue, mnémonique (rclone rc = 5572) |

Exigences de publication (portail `ca.unraid.net/submit`) : dépôt GitHub **public**, licence **OSI**, `ca_profile.xml` avec `<Profile>` non vide, **un XML par application**, balises obligatoires `Name` `Repository` `Registry` `Network` `Shell` `Privileged` `Support` `Project` `Overview` `Category` `TemplateURL`. Un scan live valide le dépôt avant soumission.

---

## 1. Contraintes Unraid dures

- **x86_64 uniquement.** Unraid OS ne tourne pas sur ARM ⇒ **build `linux/amd64` seul** pour le MVP. Économie réelle de CI ; `arm64` seulement si on vise le Docker générique plus tard.
- **appdata** : `/mnt/user/appdata/cloud-sync-manager`, monté sur `/config`.
- **Identité par défaut** : `nobody:users` = **99:100**. `PUID` / `PGID` / `UMASK` attendus (§5.3).
- **User Shares** : `/mnt/user/...`. Ne jamais toucher `/mnt/diskX` (§5.4).
- **Interdits** : `--privileged`, `/var/run/docker.sock` (§28, SEC-004).

---

## 2. Propositions par couche

### P1 — Image de base

**Recommandation : `python:3.13-slim-bookworm`, Dockerfile multi-stage, binaire rclone copié depuis l'image officielle.**

```dockerfile
FROM node:22-alpine AS web         # build Vite -> /web/dist
FROM rclone/rclone:1.71 AS rclone  # binaire officiel, versionné, vérifiable
FROM python:3.13-slim-bookworm     # runtime
COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone
```

*Pourquoi* : version de rclone **épinglée et traçable** (exigence « version rclone » du §14), glibc donc roues Python sans compilation, image ~200 Mo.

*Alternatives écartées* : `lscr.io/linuxserver/baseimage-debian` (offre s6-overlay + PUID/PGID gratuitement, mais impose un cycle de vie externe et une couche de supervision dont on n'a pas besoin — voir P3) ; Alpine/musl (roues Python à recompiler, gain de taille non déterminant).

### P2 — PUID / PGID / UMASK

**Recommandation : entrypoint maison + `gosu`.** PID 1 = notre script, qui aligne l'UID/GID, `chown` l'appdata, applique `UMASK`, puis `exec gosu` vers uvicorn.

*Pourquoi* : une trentaine de lignes testables, zéro dépendance à un framework de supervision. On n'a qu'**un** service à superviser côté init — rclone est piloté par l'application elle-même (P3), pas par s6.

### P3 — Moteur rclone : un processus par exécution ⭐ **(arbitré le 09/09/2026)**

**Décision : `subprocess` avec liste d'arguments, un processus rclone par exécution.** Pas de démon `rcd`.

```python
argv = ["rclone", "sync", src, dst,
        "--config", CONF, "--use-json-log", "--log-level", "INFO",
        "--stats", "1s", "--stats-log-level", "NOTICE"]
if dry_run:
    argv.append("--dry-run")          # même chemin de code, un flag de plus
subprocess.Popen(argv, env=env, shell=False)   # jamais shell=True
```

| Besoin cahier des charges | Commande |
|---|---|
| CLOUD-001 créer / tester / supprimer un remote | `rclone config create / update / delete`, `rclone about` |
| CLOUD-005 parcourir l'arborescence distante | `rclone lsjson` |
| SYNC-001/002 copie et miroir | `rclone copy`, `rclone sync` |
| SYNC-004 dry-run | même argv + `--dry-run` |
| UI-003 progression temps réel | ligne JSON `stats` toutes les secondes : `bytes`, `speed`, `eta`, `transfers`, `deletes`, `transferring[]` |
| §8.4 événements par fichier | lignes JSON `{"level":"info","msg":"Deleted","object":"…"}` |
| TASK-002 arrêt propre | `SIGINT` puis `SIGKILL` après délai ⇒ statut `Interrupted` (§8.5) |
| §14 version rclone | `rclone version --json` |
| SYNC-003 bidirectionnel (plus tard) | `rclone bisync` |

**Pourquoi ce choix plutôt que le démon `rcd` :**

1. **C'est ce que le cahier des charges prescrit déjà.** Le §18 ne dit pas « pas d'`argv` », il dit : *« utiliser une liste d'arguments avec `subprocess` sans `shell=True` »*. Une liste d'arguments n'est pas une concaténation shell — elle élimine par construction l'injection de métacaractères. Le gain de sécurité que j'attribuais au JSON-RPC n'existe pas : les deux approches sont sûres, et celle-ci est celle du §5.2 et du §18.
2. **Attribution correcte des événements en exécution concurrente.** C'est l'argument décisif. Le §8.4 exige une trace structurée par suppression, rattachée à son exécution. Les lignes de log JSON de rclone ne portent **pas** d'identifiant de job : avec un démon partagé et deux tâches simultanées, deux flux de suppressions se mélangent dans le même fichier et le démultiplexage devient une heuristique. Avec un processus par exécution, l'attribution est structurelle — c'est le `stdout` du processus.
3. **Isolation des pannes.** Un `rcd` figé ou mort emporte toutes les tâches. Un processus par exécution ne fait tomber que la sienne.
4. **Reproductibilité pour le diagnostic.** On peut afficher à l'utilisateur avancé la commande exacte qui a tourné (§1.3, §14). Un appel JSON-RPC n'est pas rejouable à la main.
5. **Aucune zone d'ombre de couverture d'API.** Filtres, limites de bande passante et options par tâche sont des flags documentés, testés par des millions d'utilisateurs. Via `rcd`, ce sont des surcharges `_config` / `_filter` par appel, moins documentées, à revalider à chaque montée de version.

**Précaution obligatoire :** `argv` est lisible dans `/proc` par tout processus du conteneur ⇒ **jamais de mot de passe ni de jeton en argument**. Les secrets passent par le `rclone.conf` chiffré et par l'environnement (`RCLONE_CONFIG_PASS`), voir P8.

**Ce qu'on garde de l'idée `rcd`** : l'abstraction. Toute la logique métier parle à un `RcloneAdapter` qui expose `run()`, `dry_run()`, `stop()`, `list()`, `test_remote()`. Le mécanisme reste remplaçable — si un besoin futur (OAuth interactif piloté, pool de connexions) justifiait `rcd`, seul l'adaptateur changerait.

### P4 — Un seul conteneur, un seul port

FastAPI sert l'API **et** le build Vite (`StaticFiles`, fallback SPA). `rclone rcd` reste sur la loopback interne, **jamais publié**. Un seul mapping de port dans le template : `3572`.

### P5 — Progression temps réel

`sse-starlette`, un flux `GET /api/events` ; côté serveur, une boucle qui interroge `core/stats` chaque seconde et fusionne avec le tail du JSONL. Pas de WebSocket (§5.2).

### P6 — Persistance

**Recommandation : SQLite en mode WAL + SQLAlchemy 2.0 (typé) + Alembic.**

*Pourquoi Alembic* : le §27.7 impose « pour toute migration SQLite, écrire un test de migration depuis la version précédente ». Alembic donne le versionnement, le downgrade et un harnais de test standard, ce qui sert directement **DATA-004** et **UPDATE-002**.

*Alternative légère* : `sqlite3` nu + scripts numérotés pilotés par `PRAGMA user_version` (une cinquantaine de lignes, zéro dépendance). Défendable vu les 6 tables du §6, à choisir si on veut minimiser les dépendances.

### P7 — Planificateur

**Recommandation : colonne `next_run_at` en base + une boucle `asyncio` unique, avec une horloge injectable.**

*Pourquoi pas APScheduler* : la vraie difficulté du §11 n'est pas le déclenchement, c'est la **politique de rattrapage explicite** (« une tâche manquée ne doit pas se lancer automatiquement au démarrage »). Avec une horloge injectable, on la teste au fake clock : conteneur arrêté 6 h, 3 occurrences manquées, une seule ou zéro exécution selon la politique. Avec APScheduler ce comportement dépend de `misfire_grace_time` + `coalesce`, plus difficile à prouver.

APScheduler reste une alternative acceptable (rien au §28 ne l'interdit) si on préfère une brique existante.

### P8 — Secrets

**Recommandation : `rclone.conf` chiffré (`RCLONE_CONFIG_PASS`), clé dans `/config` en `0600`, optionnellement dérivée d'une phrase de passe utilisateur.**

Le §6.1 dit explicitement que `rclone obscure` n'est pas une protection cryptographique. Le chiffrement natif de la config rclone est la réponse directe à **SEC-001**. Avec une phrase de passe utilisateur, un appdata volé ne suffit plus ; sans, on protège au moins contre la lecture accidentelle et les sauvegardes.

**Filtre de redaction centralisé** (SEC-002) : un unique point de passage traversé par les logs, l'API, les exports et le diagnostic — testé avec des jetons réalistes (`Bearer`, `?sig=`, `client_secret`, jeton JSON rclone).

### P9 — OAuth en conteneur headless ⚠️ point ouvert

Google Drive, OneDrive et Dropbox exigent un aller-retour navigateur. `rclone authorize` ouvre un écouteur sur `127.0.0.1:53682`, et les URI de redirection enregistrées par rclone pointent sur cette loopback — le navigateur doit donc se trouver dans le **même espace réseau** que le processus rclone. Trois options, aucune parfaite :

| Option | Fonctionne | Coût pour l'utilisateur |
|---|---|---|
| **A.** L'utilisateur lance `rclone authorize "drive"` sur son PC et colle le jeton dans notre UI | Toujours | Une commande hors Unraid — contredit « aucune dépendance au terminal » (§1.4), mais **sur le poste client, pas sur le serveur** |
| **B.** Publier le port 53682 du conteneur et y guider le navigateur | Souvent, selon le réseau Docker et le fournisseur | Un port de plus dans le template, échoue en `br0`/VLAN |
| **C.** `client_id` / `client_secret` propres au projet, avec une URI de redirection publique | Toujours | Impose un domaine et une revue OAuth chez chaque fournisseur, charge de maintenance réelle |

**Proposition : A par défaut (fiable, documentable, testable), B offert en raccourci quand la topologie s'y prête, C étudié seulement si l'application devient populaire.** C'est le vrai point dur du parcours **FIRST-002 / CLOUD-003 / SEC-003**, à arbitrer avant le J2.

### P10 — Notifications Unraid ✅ **(éprouvé sur serveur réel le 09/09/2026)**

**Recommandation : l'API GraphQL officielle Unraid (intégrée à l'OS depuis la 7.2), en-tête `x-api-key`, endpoint `http://<ip-serveur>/graphql`.**

L'ancienne méthode — monter `/usr/local/emhttp` et appeler le script `notify` — imposerait un montage du système hôte, contraire au §5.3 et à **SEC-004**. L'API officielle ne demande qu'une clé, saisie par l'utilisateur dans nos paramètres, et se dégrade proprement : pas de clé ⇒ notifications désactivées, jamais d'échec de tâche. Répond à **NOTIF-002**.

**Validé.** La mutation `notifyIfUnique` est acceptée avec `title`, `subject`, `description` et `importance`, et la permission `NOTIFICATIONS:CREATE_ANY` suffit — le rôle `admin` n'est pas nécessaire. La **question 5 du §25** se referme dès confirmation de l'affichage côté Unraid.

**L'obstacle n'était pas là où on le cherchait.** Un serveur Unraid en HTTPS présente par défaut un **certificat auto-signé** : le client refusait la connexion avant même de l'établir, et l'on soupçonnait la mutation alors que la requête ne partait jamais. D'où le réglage « accepter un certificat auto-signé », désactivé par défaut. À retenir pour tout futur canal sortant vers une machine du réseau local — et à retenir aussi que l'interface doit rendre la cause, faute de quoi le diagnostic passe par les journaux du conteneur.

### P11 — Protection de la WebUI (SEC-006)

Mot de passe unique haché en **Argon2id**, cookie de session `HttpOnly` + `SameSite=Lax`, activation optionnelle mais **proposée dès l'assistant de première utilisation**. En-têtes `X-Content-Type-Options`, `X-Frame-Options`, CSP stricte. TLS délégué au reverse proxy (SWAG, Nginx Proxy Manager), documenté.

### P12 — Distribution

- **GHCR**, tags `1.2.3` + `1.2` + `latest`, labels OCI (source, version, révision).
- `HEALTHCHECK` sur `/api/health` — Unraid affiche l'état de santé dans son interface.
- Dépôt **séparé** `unraid-templates` contenant `ca_profile.xml`, `cloud-sync-manager.xml` et l'icône PNG 512×512 servie en URL brute GitHub, comme le fait backrest.
- Le §17 rappelle que les réglages utilisateur sont figés dans le XML côté serveur : **ne jamais renommer une variable ou une cible de volume** après publication, seulement en ajouter.

### P13 — Cloud → Local : `/mnt/user` en écriture et paquet de garde-fous ⭐ **(arbitré 09/09/2026)**

**Décision : `/mnt/user` monté en `rw`, sur le même chemin dans le conteneur.** N'importe quel dossier d'un remote peut être synchronisé vers n'importe quel dossier de `/mnt/user`.

```xml
<Config Name="Shares Unraid" Target="/mnt/user" Default="/mnt/user"
        Mode="rw" Type="Path" Display="always" Required="true"/>
```

Chemin identique des deux côtés du montage : ce que l'UI affiche est exactement ce que l'utilisateur voit dans Unraid, ce qu'exige le §10.4 (« les chemins source et destination restent visibles »). Un utilisateur qui veut se restreindre remplace ce mapping par un ou plusieurs shares précis — le mode restrictif du §5.4 reste possible, il n'est simplement plus le défaut.

**Conséquence assumée : il n'y a plus de garde-fou noyau.** Un Miroir Cloud → Local mal configuré peut supprimer des données locales. Toute la protection repose désormais sur l'application, donc elle doit être réelle et testée (§20.3).

| # | Garde-fou | Détail |
|---|---|---|
| 1 | **Quarantaine par défaut** | Miroir Cloud → Local exécuté avec `--backup-dir /mnt/user/<share>/.cloudsync-trash/<horodatage>` : rclone **déplace** au lieu de supprimer. Le miroir devient réversible. Répond à **CONF-004** et tranche la **question 8 du §25** par oui. Rétention purgeable configurable |
| 2 | **Destinations interdites** | `appdata`, `system`, `domains`, le `/config` de l'application, et la racine `/mnt/user` elle-même — refus à la création de la tâche, pas à l'exécution |
| 3 | **Seuil de suppression bas par défaut** | Sur Cloud → Local en Miroir, seuil plus strict que Local → Cloud (§8.3) ⇒ `Bloqué — validation nécessaire` |
| 4 | **Source vide = refus** | Remote listant 0 objet ou injoignable ⇒ échec sans propager (§8.2), vérifié par `rclone about` + `lsjson` avant le run |
| 5 | **Chevauchement entre tâches** | Détecter deux tâches dont les chemins locaux s'imbriquent, a fortiori en sens opposés (aller-retour de suppressions) |
| 6 | **Chemin réel** | `realpath` sous une racine autorisée, refus de `..` et des liens symboliques qui s'en échappent (**LOCAL-005**), droits d'écriture vérifiés avant lancement (**LOCAL-003**) |
| 7 | **Propriété des fichiers écrits** | `PUID=99` / `PGID=100` (`nobody:users`) et `UMASK=000` par défaut, sinon les fichiers descendus du Cloud deviennent inaccessibles depuis les partages SMB |

---

## 3. Points à arbitrer

| # | Question | Impact | Proposition |
|---|---|---|---|
| 1 | ~~`rclone rcd` ou `subprocess` par exécution ?~~ | Structure de tout le moteur | ✅ **Arbitré 09/09/2026 : `subprocess`, un processus par exécution** (P3) |
| 2 | ~~Stratégie OAuth~~ | FIRST-002, CLOUD-003, parcours d'entrée | ✅ **Arbitré 09/09/2026 : A par défaut, B en raccourci** (P9) — l'assistant met en avant S3 / B2 / WebDAV / SFTP (clé, tout en UI) ; Drive / OneDrive / Dropbox via `rclone authorize` sur le PC de l'utilisateur + collage du jeton. Le jeton est écrit dans le `rclone.conf` par l'application, jamais passé en `argv` |
| 3 | ~~Mapping `/mnt/user` global ou shares explicites ? (§25 Q3)~~ | LOCAL-001/002/005, UNRAID-002 | ✅ **Arbitré 09/09/2026 : `/mnt/user` en `rw`** + paquet de garde-fous applicatifs (P13) |
| 4 | Alembic ou migrations `user_version` ? | DATA-004, UPDATE-002 | **Alembic** (P6) |
| 5 | Port par défaut | UNRAID-002 | **3572** (§0) |
| 6 | Nom définitif (UNRAID-006) | Publication | `Cloud Sync Manager` est **libre** dans CA |

---

## 4. Cohérence avec le cahier des charges

Aucune de ces propositions n'entre dans les interdits du **§28**, et aucune ne dévie du **§5.2** : après arbitrage, P3 applique littéralement « rclone exécuté comme processus enfant contrôlé » et le §18 « liste d'arguments avec `subprocess` sans `shell=True` ». Il n'y a donc **aucun changement de stack à justifier** — les propositions restantes portent sur des briques que le §5.2 ne fixait pas (image de base, gestion des droits, migrations, planificateur, chiffrement de la configuration, notifications, publication).
