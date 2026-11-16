"""Package de diagnostic (LOG-004, DATA-005, SEC-002, §14).

Un fichier que l'utilisateur peut joindre à une demande d'aide, sur un
forum public le plus souvent. Deux niveaux de protection, qui ne se
confondent pas :

**Les secrets ne sortent jamais**, quel que soit le mode. Les options des
stockages ne sont pas lues du tout (elles vivent dans le ``rclone.conf``),
les réglages secrets sont remplacés par « configuré », et toute chaîne qui
sort — message d'erreur rclone compris — passe par le filtre centralisé de
:mod:`csm.security.redaction`. Une URL de webhook n'est jamais recopiée :
celles de Discord ou de Slack portent leur jeton *dans le chemin*, là où
aucun filtre par motif ne le reconnaîtrait.

**L'anonymisation, activée par défaut**, retire en plus ce qui identifie la
personne sans être un secret : noms des tâches et des stockages, dossiers,
noms de fichiers, adresse du serveur Unraid. Chaque nom reçoit un jeton
stable (``tâche-1``, ``d3``, ``f12.jpg``) : deux tâches qui partagent un
dossier le partagent encore dans le diagnostic, et l'extension d'un fichier
— souvent la clé d'un problème de filtre — est conservée.

Limite assumée : un nom que l'application ne connaît pas et que rclone cite
dans un message d'erreur (un dossier intermédiaire, par exemple) peut
subsister. Le diagnostic le dit dans son en-tête, et il se relit avant
d'être publié — c'est pour cela qu'il est en JSON lisible plutôt qu'en
archive.
"""

from __future__ import annotations

import json
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from csm import __version__
from csm.config import Settings
from csm.db.models import FilterSet, Remote, Task, TaskEvent, TaskRun
from csm.rclone.probe import rclone_version
from csm.security.redaction import redact, redact_mapping
from csm.services import settings_store

FORMAT_VERSION = 1

#: Exécutions relevées par tâche : de quoi voir une tendance, pas tout
#: l'historique.
RUNS_PER_TASK = 10

#: Événements relevés par exécution. Seuls ceux qui expliquent un échec sont
#: retenus : erreurs, avertissements, nouvelles tentatives, conflits.
EVENTS_PER_RUN = 50
DIAGNOSTIC_KINDS = ("error", "warning", "retry", "conflict")

#: Réglages d'infrastructure recopiés tels quels. Liste blanche : un réglage
#: ajouté plus tard n'entre dans le diagnostic que si on l'y met exprès.
INFRA_SETTINGS = (
    "port",
    "bidirectional_enabled",
    "quarantine_retention_days",
    "quarantine_keep_runs",
    "history_retention_days",
    "history_keep_runs",
    "retry_delays_minutes",
    "scheduler_enabled",
    "scheduler_poll_seconds",
    "log_level",
)

CONFIGURED = "configuré"
NOT_CONFIGURED = "non configuré"


class Anonymiser:
    """Remplace les noms par des jetons stables, ou ne fait rien."""

    def __init__(self, enabled: bool, roots: tuple[str, ...]) -> None:
        self.enabled = enabled
        # Les racines autorisées sont des conventions Unraid (/mnt/user), pas
        # des informations personnelles : on les garde, elles situent un
        # chemin par rapport au montage du conteneur.
        self._roots = tuple(sorted((r.rstrip("/") for r in roots if r), key=len, reverse=True))
        self._tokens: dict[tuple[str, str], str] = {}
        self._counters: dict[str, int] = {}
        #: Tout ce qui a été remplacé, pour le retirer aussi des messages.
        self._seen: dict[str, str] = {}

    def token(self, kind: str, value: str) -> str:
        if not self.enabled or not value:
            return value
        key = (kind, value)
        if key not in self._tokens:
            self._counters[kind] = self._counters.get(kind, 0) + 1
            self._tokens[key] = f"{kind}-{self._counters[kind]}"
            self._seen.setdefault(value, self._tokens[key])
        return self._tokens[key]

    def _segment(self, segment: str) -> str:
        return self.token("d", segment)

    def _file(self, name: str) -> str:
        if not self.enabled or not name:
            return name
        suffix = PurePosixPath(name).suffix
        # Une « extension » de vingt caractères est un morceau de nom.
        suffix = suffix if 1 < len(suffix) <= 8 else ""
        anonymous = f"{self.token('f', name)}{suffix}"
        self._seen[name] = anonymous
        return anonymous

    def local_path(self, path: str | None) -> str | None:
        if not self.enabled or not path:
            return path
        normalised = path.replace("\\", "/")
        for root in self._roots:
            if normalised == root or normalised.startswith(root + "/"):
                rest = normalised[len(root) :].strip("/")
                return "/".join([root, *(self._segment(p) for p in rest.split("/") if p)])
        return "/" + "/".join(self._segment(p) for p in normalised.split("/") if p)

    def remote_path(self, path: str | None) -> str | None:
        if not self.enabled or not path:
            return path
        return "/".join(self._segment(p) for p in path.split("/") if p)

    def file_path(self, path: str | None) -> str | None:
        """Chemin relatif d'un fichier dans une exécution."""
        if not self.enabled or not path:
            return path
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        if not parts:
            return path
        return "/".join([*(self._segment(p) for p in parts[:-1]), self._file(parts[-1])])

    def text(self, value: str | None) -> str | None:
        """Secrets masqués, puis noms connus remplacés par leur jeton."""
        cleaned = redact(value)
        if not self.enabled or not cleaned:
            return cleaned
        # Les plus longs d'abord, pour qu'un nom n'en ampute pas un autre ;
        # les noms très courts sont laissés, ils remplaceraient n'importe quoi.
        for original in sorted(self._seen, key=len, reverse=True):
            if len(original) < 3:
                continue
            cleaned = re.sub(
                rf"(?<![\w.-]){re.escape(original)}(?![\w-])",
                self._seen[original],
                cleaned,
            )
        return cleaned


def build(
    session: Session,
    settings: Settings,
    *,
    anonymise: bool = True,
    live_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    names = Anonymiser(anonymise, tuple(str(root) for root in settings.roots))

    remotes = list(session.scalars(select(Remote).order_by(Remote.name)).all())
    tasks = list(session.scalars(select(Task).order_by(Task.name)).all())
    filter_sets = list(session.scalars(select(FilterSet).order_by(FilterSet.name)).all())

    # Tous les noms connus sont enregistrés avant de traiter le moindre
    # message : l'erreur d'une tâche peut citer le dossier d'une autre.
    for remote in remotes:
        names.token("stockage", remote.name)
        names.token("section", remote.rclone_remote_name)
    for task in tasks:
        names.token("tâche", task.name)
        names.local_path(task.local_path)
        names.remote_path(task.remote_path)

    remote_entries = [
        {
            "id": remote.id,
            "name": names.token("stockage", remote.name),
            # Le nom de section rclone dérive du nom affiché : il l'identifie
            # tout autant.
            "rclone_remote_name": names.token("section", remote.rclone_remote_name),
            "provider": remote.provider,
            "status": remote.status,
            "last_test_at": _iso(remote.last_test_at),
            "capabilities": _capabilities(remote.capabilities_json),
        }
        for remote in remotes
    ]

    task_entries = [_task(session, task, names) for task in tasks]

    filter_entries = [
        {
            "id": item.id,
            "name": names.token("filtre", item.name),
            # Les règles sont le cœur d'un problème de filtre ; un motif peut
            # pourtant nommer un dossier. En mode anonyme, on garde leur
            # forme (type de règle) et non leur valeur.
            "rules": _rules(item.rules_json, anonymise),
        }
        for item in filter_sets
    ]

    stored = settings_store.public_settings(session)
    preferences = {
        "notifications": {
            "unraid": (
                CONFIGURED if stored.get("unraid_url") else NOT_CONFIGURED
            )
            if anonymise
            else (stored.get("unraid_url") or NOT_CONFIGURED),
            "unraid_api_key": (
                CONFIGURED if stored.get("unraid_api_key_configured") else NOT_CONFIGURED
            ),
            # Jamais recopiée : le jeton d'un webhook Discord ou Slack est
            # dans le chemin de l'URL.
            "webhook": CONFIGURED if stored.get("webhook_url") else NOT_CONFIGURED,
            "events": stored.get("events"),
            "allow_self_signed": stored.get("allow_self_signed"),
        },
        "logging": settings_store.logging_settings(session),
    }

    info = rclone_version(settings.rclone_binary)
    payload: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "anonymised": anonymise,
        "notice": (
            "Aucun identifiant ni mot de passe n'est inclus. "
            + (
                "Noms, dossiers et fichiers sont remplacés par des jetons ; "
                "un nom cité par rclone dans un message d'erreur peut "
                "subsister : relisez avant de publier."
                if anonymise
                else "Noms, dossiers et fichiers sont en clair : ce fichier "
                "n'est pas destiné à être publié."
            )
        ),
        "versions": {
            "application": __version__,
            "rclone": info.version if info else None,
            "rclone_platform": f"{info.os}/{info.arch}" if info else None,
            "python": sys.version.split()[0],
            "system": platform.platform(terse=True),
        },
        "infrastructure": {
            **{key: getattr(settings, key) for key in INFRA_SETTINGS},
            "allowed_roots": [str(root) for root in settings.roots],
            "rclone_config_encrypted": bool(settings.rclone_config_password),
        },
        "preferences": preferences,
        "remotes": remote_entries,
        "filter_sets": filter_entries,
        "tasks": task_entries,
        "live_runs": [
            {
                "run_id": run.get("run_id"),
                "task_id": run.get("task_id"),
                "phase": run.get("phase"),
                "dry_run": run.get("dry_run"),
                "started_at": run.get("started_at"),
            }
            for run in (live_runs or [])
        ],
    }
    # Dernier filet : toute clé au nom de secret, où qu'elle soit, est
    # masquée sans regarder sa valeur.
    return redact_mapping(payload)


def _task(session: Session, task: Task, names: Anonymiser) -> dict[str, Any]:
    runs = list(
        session.scalars(
            select(TaskRun)
            .where(TaskRun.task_id == task.id)
            .order_by(TaskRun.started_at.desc())
            .limit(RUNS_PER_TASK)
        ).all()
    )
    remote = session.get(Remote, task.remote_id)
    return {
        "id": task.id,
        "name": names.token("tâche", task.name),
        "remote": names.token("stockage", remote.name) if remote else None,
        "local_path": names.local_path(task.local_path),
        "remote_path": names.remote_path(task.remote_path),
        "direction": task.direction,
        "mode": task.mode,
        "enabled": task.enabled,
        "status": task.status,
        "dry_run_required": task.dry_run_required,
        "quarantine_enabled": task.quarantine_enabled,
        "max_deletes": task.max_deletes,
        "max_delete_percent": task.max_delete_percent,
        "has_filter_set": task.filter_set_id is not None,
        "schedule": _json(task.schedule_json),
        "transfer_settings": _json(task.bandwidth_json),
        "bisync": _json(task.bisync_json),
        "next_run_at": _iso(task.next_run_at),
        "runs": [_run(session, run, names) for run in runs],
    }


def _run(session: Session, run: TaskRun, names: Anonymiser) -> dict[str, Any]:
    events = session.scalars(
        select(TaskEvent)
        .where(TaskEvent.run_id == run.id, TaskEvent.kind.in_(DIAGNOSTIC_KINDS))
        .order_by(TaskEvent.id)
        .limit(EVENTS_PER_RUN)
    ).all()
    # Les chemins d'abord : ils enrichissent le dictionnaire de noms que
    # le filtrage des messages utilisera.
    paths = [names.file_path(event.path) for event in events]
    summary = _json(run.summary_json) or {}
    if isinstance(summary, dict) and "last_error" in summary:
        summary["last_error"] = names.text(summary.get("last_error"))
    if isinstance(summary, dict) and "blocked_reason" in summary:
        summary["blocked_reason"] = names.text(summary.get("blocked_reason"))
    return {
        "id": run.id,
        "started_at": _iso(run.started_at),
        "ended_at": _iso(run.ended_at),
        "status": run.status,
        "exit_code": run.exit_code,
        "dry_run": run.dry_run,
        "rclone_version": run.rclone_version,
        "transferred_files": run.transferred_files,
        "transferred_bytes": run.transferred_bytes,
        "deleted_files": run.deleted_files,
        "errors_count": run.errors_count,
        "summary": summary,
        "events": [
            {
                "at": _iso(event.at),
                "kind": event.kind,
                "path": path,
                "message": names.text(event.message),
            }
            for event, path in zip(events, paths)
        ],
    }


def _rules(raw: str | None, anonymise: bool) -> list[Any]:
    rules = _json(raw) or []
    if not anonymise or not isinstance(rules, list):
        return rules
    return [
        {"type": rule.get("type"), "value": "…"} if isinstance(rule, dict) else "…"
        for rule in rules
    ]


def _capabilities(raw: str | None) -> Any:
    capabilities = _json(raw)
    if isinstance(capabilities, dict):
        # Le quota décrit l'espace d'une personne, pas le fonctionnement du
        # stockage ; sa présence suffit au diagnostic.
        if "quota" in capabilities:
            capabilities["quota"] = "relevé"
    return capabilities


def _json(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return "illisible"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def filename(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    return f"cloud-sync-manager-diagnostic-{stamp}.json"


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
