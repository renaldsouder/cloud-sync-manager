<p align="center">
  <img src="unraid/images/cloud-sync-manager.png" width="120" alt="Cloud Sync Manager">
</p>

<h1 align="center">Cloud Sync Manager</h1>

<p align="center">
  Cloud synchronisation for Unraid — pick a remote, pick a folder, pick a
  direction, schedule it, and let it run. No command line.
</p>

<p align="center">
  <a href="README.fr.md">🇫🇷 Version française</a> ·
  <a href="https://rclone.org">Powered by rclone</a> ·
  <a href="LICENSE">GPL-3.0-or-later</a>
</p>

---

Unraid stores data well, but syncing a share to a cloud provider means
assembling tools, learning commands, or installing a generic interface that was
not designed for Unraid. Cloud Sync Manager aims at the experience of Synology
Cloud Sync: choose a provider, choose a folder, choose a direction, done.

One container. One web interface. rclone underneath.

## Maturity — read this before installing

Version **0.2.3**. It is published, it runs, and the destructive guards below
are real. It is also a project whose first commit is dated 9 September 2026,
and whose own success criterion has barely started running.

| | |
|---|---|
| Destructive guards (threshold, unreachable source, trash, dry run) | Automated **and** integration tests — but against a local folder standing in for the cloud |
| Tested with a real account | **OneDrive only** |
| Google Drive, Dropbox | OAuth path implemented, tested up to the consent screen, never completed with a real account |
| S3, B2, WebDAV, SFTP | Implemented, **never exercised against a real service** |
| Unraid notifications | Validated against a real Unraid 7.3.2 |
| 30 days of real use — the project's own success criterion | Under way, started 9 September 2026 |
| Bidirectional sync | Deliberately absent until its destructive test matrix is complete |
| English web interface | Not done — **the interface is French only** |

Two things follow from that table.

**A local folder cannot reproduce what a cloud does badly** — unreliable
listings, rate limits, eventual consistency. Those are precisely the conditions
the fail-closed rules exist for, and they have not been met in the wild yet.

**Real use finds what the test suite cannot.** Six defects have already surfaced
that 243 passing tests did not catch: an OAuth port that could never have
worked, accented filenames corrupted by locale decoding, runs displayed as
running forever. Expect more. Start with **Copy**, on data you have a backup of
— not with **Mirror** on your only copy.

## What makes it different

Plenty of tools can copy files to a cloud. The value here is in what happens
when something goes wrong.

**Nothing destructive is silent.** A mirror run measures what it would delete
*before* touching anything. Past a configurable threshold the task stops in a
`Blocked` state and asks you to confirm — and the button names the real action,
"Delete 423 files", never "Confirm". The files are listed.

**A missing source never becomes a mass deletion.** If the source is
unreachable, or empty while the destination is not, the task fails without
propagating anything. That is the unmounted-share scenario, and it is the one
that destroys data in tools that assume "empty means delete everything".

**Deletions are reversible.** They are moved to a `.cloudsync-trash` folder at
the destination, purged on a retention you control. Overwritten versions go
there too.

**An interrupted run is never reported as successful.** Stop it, or crash the
container mid-transfer, and the run is recorded as `Interrupted`.

**Simulation before destruction.** A mirror's first run requires a dry run, and
so does any change of source, destination or direction.

**Cloud credentials never leak.** They live in rclone's config file, never in
the database, never in a command-line argument, never in a log, never in a
configuration export.

## Installing on Unraid

### From Community Applications

Search for `Cloud Sync Manager` in the **Apps** tab.

### From the template URL

**Docker → Add Container**, and paste this into the *Template* field:

```
https://raw.githubusercontent.com/renaldsouder/unraid-templates/main/cloud-sync-manager.xml
```

### By hand

| Setting | Value |
|---|---|
| Repository | `ghcr.io/renaldsouder/cloud-sync-manager:latest` |
| Network | `bridge` |
| Port | `3572` → `3572` |
| Path | `/config` → `/mnt/user/appdata/cloud-sync-manager` |
| Path | `/mnt/user` → `/mnt/user` |
| Variables | `PUID=99`, `PGID=100`, `UMASK=000`, `TZ=Europe/Paris` |

The shares path is mapped **to the same path on both sides** so that what the
interface shows is exactly what you see in Unraid. `appdata`, `system` and
`domains` can never be a sync *destination*, whatever you configure.

Then open the WebUI and set a password under **Paramètres → Accès à
l'interface**. Until you do, the application warns you on every screen that
anyone on your network can trigger a deletion.

## Connecting a cloud provider

Two kinds of providers, two experiences.

See the maturity table above for which of these have actually been exercised
against a real account.

### Key or password — S3, Backblaze B2, WebDAV, SFTP

Everything is typed into the interface. Add the storage, fill the fields, test
it. Nothing else to install.

### Browser authorisation — Google Drive, OneDrive, Dropbox

These three require an OAuth round trip through a browser. It is driven from
the interface — you never install rclone or open a terminal.

1. **Stockages Cloud → Ajouter un stockage**, pick the provider.
2. Click **Autoriser l'accès**. A tab opens on the provider's sign-in page.
3. Sign in and grant access.
4. Your browser lands on a **connection-error page at `localhost:53682`**.
   **This is expected.** The redirect address registered by rclone with the
   providers points at `localhost`, which is *your* machine, not the server —
   so nothing answers there.
5. **Copy the full address of that error page** from the address bar. It looks
   like `http://localhost:53682/?code=…&state=…`.
6. Paste it into the field in the interface and click **Terminer
   l'autorisation**.

The token is filled in automatically. For OneDrive, `drive_id` and `drive_type`
are too — the application asks Microsoft for them on your behalf.

> **If OneDrive reports a missing drive id**, the storage is still created with
> its token, and the list shows a **Compléter la configuration** button that
> queries Microsoft again. No need to redo the authorisation.

Removing that final copy-paste would require registering our own OAuth
application with each provider, with the public domain and review process that
implies. One paste is the best available trade today.

**Always test a storage after creating it.** A successful connection does not
mean every operation is supported by that provider.

The token is written straight into the container's `rclone.conf`. It is never
displayed again, and never included in a configuration backup. The longer
walkthrough, with the failure cases, is in
[the installation guide](docs/Installation_Unraid.md#2-ajouter-un-stockage-cloud)
(French).

## First synchronisation

Start with **Copie** — it adds and updates, and never deletes anything at the
destination. Run the **simulation** first: it lists exactly what would be
transferred, without writing.

Only move to **Miroir** once a few copies have run cleanly. Its first run will
require a simulation anyway.

## Configuration

All persistent state lives under `/config` — the SQLite database, the rclone
configuration, the logs and the applied filter sets.

| Variable | Default | Purpose |
|---|---|---|
| `CSM_CONFIG_DIR` | `/config` | Appdata root |
| `CSM_PORT` | `3572` | Web interface port |
| `CSM_ALLOWED_ROOTS` | `/mnt/user` | Comma-separated local roots no task may escape |
| `CSM_HISTORY_RETENTION_DAYS` | `90` | Age past which a run is purged |
| `CSM_HISTORY_KEEP_RUNS` | `200` | Runs kept per task regardless of age |
| `CSM_QUARANTINE_RETENTION_DAYS` | `30` | Age past which a trash batch is purged |
| `CSM_QUARANTINE_KEEP_RUNS` | `3` | Trash batches always kept, regardless of age |
| `CSM_SCHEDULER_POLL_SECONDS` | `20` | Scheduler heartbeat |
| `TZ` | — | Server timezone; schedule times refer to it |
| `PUID` / `PGID` | `99` / `100` | Owner of written files (`nobody:users`) |
| `UMASK` | `000` | Permissions of written files |

## Features

- **Directions** — Local → Cloud and Cloud → Local
- **Modes** — Copy (never deletes) and Mirror (guarded, see above)
- **Scheduling** — every N minutes, daily at a fixed time, or chosen weekdays.
  No cron expression. Explicit catch-up policy after a server restart.
- **Filters** — ordered include/exclude rules by path, extension, name pattern,
  hidden files and size, with a preview tool that shows which rule decided
- **Live progress** — current file, throughput, ETA, over SSE
- **History** — per-run result, exit code, transferred and deleted files, rclone
  version, with configurable retention
- **Per-file detail** — open a run to see every file transferred, deleted,
  skipped or in error, searchable by path. A truncated list says so, loudly:
  not finding a file there never means it was not handled.
- **Notifications** — Unraid's built-in API and a generic webhook
- **Backup** — export and restore the configuration, credentials excluded by
  design; restored tasks arrive paused
- **Bidirectional** — deliberately **not** available until its destructive test
  matrix is complete. Half-reliable, it would lose data.

## Development

Backend integration tests need the rclone binary. Drop it in `backend/.tools/`
(git-ignored) or point `CSM_RCLONE_BINARY` at it; otherwise those tests are
skipped rather than failed.

```bash
cd backend
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest
CSM_CONFIG_DIR=./dev-appdata .venv/bin/python -m uvicorn csm.main:app --reload --port 3572
```

On Windows the virtualenv puts them in `.venv/Scripts/` instead of
`.venv/bin/`.

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api to port 3572
npm run build      # tsc -b && vite build
```

```bash
docker build -f docker/Dockerfile -t cloud-sync-manager .
```

```
backend/    FastAPI API, SQLite model, Alembic migrations, rclone adapter
frontend/   React + TypeScript web interface (Vite)
docker/     Multi-stage Dockerfile, PUID/PGID entrypoint
unraid/     Community Applications template, repository profile, icon
docs/       Specification, technical decisions, installation guide
```

The specification, the technical decision record and the installation guide
are in French, under [`docs/`](docs/):

- [Installation and usage guide](docs/Installation_Unraid.md)
- [Specification](docs/Cloud_Sync_Manager_Cahier_des_charges.md)
- [Technical decisions](docs/Propositions_Techniques_Unraid.md)

## License

**GPL-3.0-or-later** for the application ([`LICENSE`](LICENSE)) — a derived
version must stay open, which protects the data-loss guards rather than letting
anyone close them off.

**MIT** for the Unraid template repository ([`unraid/LICENSE`](unraid/LICENSE)),
which is only descriptive XML.

Both are OSI-approved, as Community Applications requires. rclone is MIT and is
not linked into the code — the application runs it as a child process.
