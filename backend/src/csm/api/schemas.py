"""Schémas d'API.

Aucun schéma de sortie ne comporte de champ secret : un jeton enregistré
n'est jamais renvoyé, même masqué partiellement (§6.1, SEC-001).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from csm.db.models import Remote
from csm.services.schedule import Schedule, describe


class ProviderOption(BaseModel):
    name: str
    help: str
    help_full: str
    type: str
    required: bool
    advanced: bool
    is_password: bool
    sensitive: bool
    default: str | None = None
    examples: list[dict[str, Any]] = Field(default_factory=list)


class ProviderOut(BaseModel):
    name: str
    label: str
    description: str
    priority: bool
    needs_oauth: bool
    options: list[ProviderOption]


class RemoteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=64)
    options: dict[str, str] = Field(default_factory=dict)


class RemoteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    options: dict[str, str] | None = None


class RemoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    provider: str
    rclone_remote_name: str
    status: str
    last_test_at: datetime | None = None
    capabilities: dict[str, Any] | None = None
    task_count: int = 0
    options: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def build(
        cls,
        remote: Remote,
        *,
        options: dict[str, str] | None = None,
        task_count: int = 0,
    ) -> "RemoteOut":
        capabilities: dict[str, Any] | None = None
        if remote.capabilities_json:
            try:
                capabilities = json.loads(remote.capabilities_json)
            except ValueError:
                capabilities = None
        return cls(
            id=remote.id,
            name=remote.name,
            provider=remote.provider,
            rclone_remote_name=remote.rclone_remote_name,
            status=remote.status,
            last_test_at=remote.last_test_at,
            capabilities=capabilities,
            task_count=task_count,
            options=options or {},
        )


class RemoteTestOut(BaseModel):
    ok: bool
    detail: str
    elapsed_ms: int
    capabilities: dict[str, Any]
    entries: list[str]


class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    remote_id: str
    local_path: str
    remote_path: str = ""
    direction: str = "local_to_remote"
    mode: str = "copy"
    schedule: dict[str, Any] | None = None
    filter_set_id: str | None = None
    bandwidth: dict[str, Any] | None = None


class TaskUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    local_path: str | None = None
    remote_path: str | None = None
    schedule: dict[str, Any] | None = None
    enabled: bool | None = None
    filter_set_id: str | None = None
    bandwidth: dict[str, Any] | None = None


class TaskRunStart(BaseModel):
    #: La simulation est le défaut : il faut demander explicitement à écrire.
    dry_run: bool = True
    #: Autorise une exécution au-delà du seuil de suppression (§8.3). Ne
    #: dispense jamais du contrôle de source : une source démontée reste
    #: refusée, quoi que l'utilisateur confirme.
    confirm_deletions: bool = False


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    remote_id: str
    local_path: str
    remote_path: str
    direction: str
    mode: str
    delete_policy: str
    quarantine_enabled: bool
    dry_run_required: bool
    enabled: bool
    status: str
    last_run_id: str | None = None
    next_run_at: datetime | None = None
    schedule: dict[str, Any] | None = None
    schedule_label: str = "manuelle"
    filter_set_id: str | None = None
    bandwidth: dict[str, Any] | None = None
    live: dict[str, Any] | None = None

    @classmethod
    def build(cls, task: Any, *, live: dict[str, Any] | None = None) -> "TaskOut":
        schedule = Schedule.parse(task.schedule_json)
        return cls(
            id=task.id,
            name=task.name,
            remote_id=task.remote_id,
            local_path=task.local_path,
            remote_path=task.remote_path,
            direction=task.direction,
            mode=task.mode,
            delete_policy=task.delete_policy,
            quarantine_enabled=task.quarantine_enabled,
            dry_run_required=task.dry_run_required,
            enabled=task.enabled,
            status=task.status,
            last_run_id=task.last_run_id,
            next_run_at=task.next_run_at,
            schedule=schedule.to_dict() if schedule.automatic else None,
            schedule_label=describe(schedule),
            filter_set_id=task.filter_set_id,
            bandwidth=(
                json.loads(task.bandwidth_json) if task.bandwidth_json else None
            ),
            live=live,
        )


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    started_at: datetime
    ended_at: datetime | None = None
    status: str
    exit_code: int | None = None
    transferred_files: int = 0
    transferred_bytes: int = 0
    deleted_files: int = 0
    errors_count: int = 0
    dry_run: bool = False
    rclone_version: str | None = None
    summary: dict[str, Any] | None = None
    live: dict[str, Any] | None = None

    @classmethod
    def build(cls, run: Any, *, live: Any = None) -> "RunOut":
        summary: dict[str, Any] | None = None
        if run.summary_json:
            try:
                summary = json.loads(run.summary_json)
            except ValueError:
                summary = None
        return cls(
            id=run.id,
            task_id=run.task_id,
            started_at=run.started_at,
            ended_at=run.ended_at,
            status=run.status,
            exit_code=run.exit_code,
            transferred_files=run.transferred_files,
            transferred_bytes=run.transferred_bytes,
            deleted_files=run.deleted_files,
            errors_count=run.errors_count,
            dry_run=run.dry_run,
            rclone_version=run.rclone_version,
            summary=summary,
            live=live.snapshot() if live is not None else None,
        )


class TaskEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    at: datetime
    path: str | None = None
    size: int | None = None
    message: str | None = None


class TaskEventSummary(BaseModel):
    """Synthèse du détail d'une exécution (LOG-002).

    Permet à l'interface d'afficher les filtres et leur cardinalité sans
    rapatrier des milliers de lignes, et de signaler une liste tronquée.
    """

    counts: dict[str, int] = {}
    total: int = 0
    truncated: bool = False


class DashboardCounters(BaseModel):
    total: int = 0
    scheduled: int = 0
    running: int = 0
    warning: int = 0
    error: int = 0
    blocked: int = 0
    paused: int = 0


class DashboardOut(BaseModel):
    counters: DashboardCounters
    next_run_at: datetime | None = None
    throughput_bytes_per_second: float = 0.0
    running: list[dict[str, Any]] = Field(default_factory=list)
    recent_runs: list[RunOut] = Field(default_factory=list)
