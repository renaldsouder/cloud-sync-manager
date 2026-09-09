"""Export et restauration de la configuration (DATA-002, DATA-003, DATA-005, §16).

Deux règles gouvernent ce module.

**Aucun secret dans l'export.** Le §16 et DATA-005 sont formels : une
sauvegarde n'est pas un fichier protégé, elle finit sur une clé USB ou dans
un dépôt. Les stockages sont donc exportés sans leurs identifiants, et la
restauration demande de les ressaisir. C'est un inconvénient assumé, pas un
oubli — l'alternative serait de disséminer des jetons Cloud en clair.

**Une restauration ne lance rien.** Les tâches importées arrivent
désactivées : le §16 exige une validation des chemins et des stockages
avant toute exécution. Restaurer sur un serveur dont les shares diffèrent ne
doit pas déclencher une synchronisation destructive au premier battement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from csm import __version__
from csm.config import Settings
from csm.db.models import FilterSet, Remote, Task
from csm.paths import PathNotAllowed, resolve_within_roots
from csm.services import settings_store

#: Version du format. Toute évolution incompatible doit l'incrémenter et
#: être accompagnée d'une migration testée (§16, UPDATE-002).
FORMAT_VERSION = 1


class BackupError(RuntimeError):
    """Sauvegarde illisible ou incompatible."""


@dataclass
class ImportReport:
    remotes_created: int = 0
    filter_sets_created: int = 0
    tasks_created: int = 0
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "remotes_created": self.remotes_created,
            "filter_sets_created": self.filter_sets_created,
            "tasks_created": self.tasks_created,
            "skipped": self.skipped,
            "warnings": self.warnings,
        }


def export_config(session: Session) -> dict[str, Any]:
    remotes = [
        {
            "id": remote.id,
            "name": remote.name,
            "provider": remote.provider,
            "rclone_remote_name": remote.rclone_remote_name,
        }
        for remote in session.scalars(select(Remote).order_by(Remote.name)).all()
    ]

    filter_sets = [
        {"id": item.id, "name": item.name, "rules": json.loads(item.rules_json or "[]")}
        for item in session.scalars(select(FilterSet).order_by(FilterSet.name)).all()
    ]

    tasks = [
        {
            "id": task.id,
            "name": task.name,
            "remote_id": task.remote_id,
            "local_path": task.local_path,
            "remote_path": task.remote_path,
            "direction": task.direction,
            "mode": task.mode,
            "delete_policy": task.delete_policy,
            "quarantine_enabled": task.quarantine_enabled,
            "max_deletes": task.max_deletes,
            "max_delete_percent": task.max_delete_percent,
            "filter_set_id": task.filter_set_id,
            "schedule": json.loads(task.schedule_json) if task.schedule_json else None,
            "bandwidth": json.loads(task.bandwidth_json) if task.bandwidth_json else None,
        }
        for task in session.scalars(select(Task).order_by(Task.name)).all()
    ]

    stored = settings_store.all_settings(session)
    settings = {
        key: value
        for key, value in stored.items()
        if key not in settings_store.SECRET_KEYS
    }

    return {
        "format_version": FORMAT_VERSION,
        "app_version": __version__,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "contains_secrets": False,
        "remotes": remotes,
        "filter_sets": filter_sets,
        "tasks": tasks,
        "settings": settings,
    }


def import_config(
    session: Session, app_settings: Settings, payload: dict[str, Any]
) -> ImportReport:
    version = payload.get("format_version")
    if not isinstance(version, int):
        raise BackupError("fichier de sauvegarde illisible : version absente")
    if version > FORMAT_VERSION:
        raise BackupError(
            f"sauvegarde au format {version}, cette version n'en comprend que "
            f"{FORMAT_VERSION} — mettez l'application à jour avant de restaurer"
        )

    report = ImportReport()

    existing_remotes = {
        remote.name: remote
        for remote in session.scalars(select(Remote)).all()
    }
    for entry in payload.get("remotes", []):
        name = str(entry.get("name", "")).strip()
        if not name or name in existing_remotes:
            report.skipped.append(f"stockage « {name} » déjà présent")
            continue
        remote = Remote(
            id=str(entry.get("id")) or None,
            name=name,
            provider=str(entry.get("provider", "")),
            rclone_remote_name=str(entry.get("rclone_remote_name", name)),
            status="unknown",
        )
        session.add(remote)
        existing_remotes[name] = remote
        report.remotes_created += 1
        report.warnings.append(
            f"« {name} » doit être réauthentifié : la sauvegarde ne contient "
            "aucun identifiant"
        )
    session.flush()

    existing_filters = {item.name for item in session.scalars(select(FilterSet)).all()}
    for entry in payload.get("filter_sets", []):
        name = str(entry.get("name", "")).strip()
        if not name or name in existing_filters:
            continue
        session.add(
            FilterSet(
                id=str(entry.get("id")) or None,
                name=name,
                rules_json=json.dumps(entry.get("rules") or []),
            )
        )
        existing_filters.add(name)
        report.filter_sets_created += 1
    session.flush()

    known_remote_ids = {remote.id for remote in session.scalars(select(Remote)).all()}
    existing_tasks = {task.name for task in session.scalars(select(Task)).all()}

    for entry in payload.get("tasks", []):
        name = str(entry.get("name", "")).strip()
        if not name or name in existing_tasks:
            report.skipped.append(f"tâche « {name} » déjà présente")
            continue
        if entry.get("remote_id") not in known_remote_ids:
            report.skipped.append(f"tâche « {name} » : stockage introuvable")
            continue

        local_path = str(entry.get("local_path", ""))
        try:
            resolve_within_roots(local_path, app_settings.roots)
        except PathNotAllowed:
            report.warnings.append(
                f"« {name} » : le chemin {local_path} n'existe pas sur ce serveur, "
                "la tâche reste désactivée jusqu'à correction"
            )

        schedule = entry.get("schedule")
        bandwidth = entry.get("bandwidth")
        session.add(
            Task(
                id=str(entry.get("id")) or None,
                name=name,
                remote_id=str(entry["remote_id"]),
                local_path=local_path,
                remote_path=str(entry.get("remote_path", "")),
                direction=str(entry.get("direction", "local_to_remote")),
                mode=str(entry.get("mode", "copy")),
                delete_policy=str(entry.get("delete_policy", "never")),
                quarantine_enabled=bool(entry.get("quarantine_enabled", True)),
                max_deletes=entry.get("max_deletes"),
                max_delete_percent=entry.get("max_delete_percent"),
                filter_set_id=entry.get("filter_set_id"),
                schedule_json=json.dumps(schedule) if schedule else None,
                bandwidth_json=json.dumps(bandwidth) if bandwidth else None,
                # §16 — jamais d'exécution automatique après une restauration.
                enabled=False,
                status="paused",
                # Le Miroir redemande une simulation : les chemins ont pu
                # changer de serveur, la précédente ne prouve plus rien.
                dry_run_required=str(entry.get("mode", "copy")) != "copy",
            )
        )
        existing_tasks.add(name)
        report.tasks_created += 1

    for key, value in (payload.get("settings") or {}).items():
        if key not in settings_store.SECRET_KEYS:
            settings_store.put(session, str(key), value)

    session.flush()
    if report.tasks_created:
        report.warnings.append(
            f"{report.tasks_created} tâche(s) importée(s) en pause : vérifiez "
            "chemins et stockages avant de les réactiver"
        )
    return report
