"""Exécution des tâches (SYNC-001, SYNC-002, SYNC-004, TASK-002, UI-003).

Un processus rclone par exécution (décision P3). L'attribution des
événements est alors structurelle : ce qui sort du ``stderr`` de ce
processus appartient à cette exécution, et à aucune autre, même quand
plusieurs tâches tournent en parallèle.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from csm.db.models import Remote, Task, TaskEvent, TaskRun, new_id
from csm.rclone import events as rclone_events
from csm.rclone.adapter import RcloneAdapter
from csm.rclone.events import RcloneEvent

logger = logging.getLogger("csm.runner")

#: Plafond d'événements fichier par fichier conservés en base. Au-delà, les
#: compteurs restent exacts mais le détail est tronqué : le §13 interdit une
#: croissance illimitée de la base (LOG-005).
MAX_STORED_EVENTS = 5000

#: Modes autorisés à ce stade. Le Miroir reste fermé tant que les garde-fous
#: du §8 et la suite destructive du §20.3 ne sont pas en place (J4).
SUPPORTED_MODES = frozenset({"copy"})

RUN_TO_TASK_STATUS = {
    "success": "success",
    "warning": "warning",
    "error": "error",
    "interrupted": "ready",
    "blocked": "blocked",
}


class RunError(RuntimeError):
    """Refus d'exécuter, message destiné à l'utilisateur."""


@dataclass
class LiveRun:
    run_id: str
    task_id: str
    task_name: str
    dry_run: bool
    started_at: datetime
    process: subprocess.Popen[str]
    status: str = "running"
    cancelled: bool = False
    current_file: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    counters: dict[str, int] = field(
        default_factory=lambda: {"transfers": 0, "deletes": 0, "errors": 0}
    )
    last_error: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "task_name": self.task_name,
            "dry_run": self.dry_run,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "current_file": self.current_file,
            "counters": dict(self.counters),
            "stats": dict(self.stats),
            "last_error": self.last_error,
        }


def endpoints(task: Task, remote: Remote) -> tuple[str, str]:
    """Source et destination, dans l'ordre attendu par rclone."""
    remote_spec = f"{remote.rclone_remote_name}:{task.remote_path}"
    if task.direction == "local_to_remote":
        return task.local_path, remote_spec
    return remote_spec, task.local_path


def requires_dry_run(task: Task) -> bool:
    """§8.1 — une simulation est exigée avant la première exécution destructive.

    Une Copie ne supprime rien : la lui imposer n'apporterait aucune sécurité
    et découragerait l'usage du garde-fou là où il compte vraiment.
    """
    return task.dry_run_required and task.mode != "copy"


class RunManager:
    """Registre des exécutions en cours."""

    def __init__(self, session_factory: sessionmaker[Session], adapter: RcloneAdapter) -> None:
        self._session_factory = session_factory
        self._adapter = adapter
        self._runs: dict[str, LiveRun] = {}
        self._lock = threading.Lock()

    # -- lecture ------------------------------------------------------------

    def get(self, run_id: str) -> LiveRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def snapshots(self) -> list[dict[str, Any]]:
        with self._lock:
            return [run.snapshot() for run in self._runs.values()]

    def is_running(self, task_id: str) -> bool:
        with self._lock:
            return any(
                run.task_id == task_id and run.status == "running"
                for run in self._runs.values()
            )

    # -- cycle de vie -------------------------------------------------------

    def start(self, task_id: str, *, dry_run: bool) -> str:
        if self.is_running(task_id):
            raise RunError("cette tâche est déjà en cours d'exécution")

        session = self._session_factory()
        try:
            task = session.get(Task, task_id)
            if task is None:
                raise RunError("tâche introuvable")
            if task.mode not in SUPPORTED_MODES:
                raise RunError(
                    f"le mode « {task.mode} » n'est pas encore disponible : "
                    "seule la Copie est active à ce stade"
                )
            if not dry_run and requires_dry_run(task):
                raise RunError(
                    "une simulation est obligatoire avant la première exécution "
                    "de cette tâche"
                )

            remote = session.get(Remote, task.remote_id)
            if remote is None:
                raise RunError("le stockage associé à cette tâche a disparu")

            source, destination = endpoints(task, remote)
            arguments = self._adapter.build_transfer_args(
                _operation_for(task.mode),
                source,
                destination,
                dry_run=dry_run,
                bwlimit=_bandwidth_limit(task),
            )

            run = TaskRun(
                id=new_id(),
                task_id=task.id,
                status="running",
                dry_run=dry_run,
                rclone_version=_version_or_none(self._adapter),
            )
            session.add(run)
            task.status = "running"
            task.last_run_id = run.id
            session.commit()
            run_id = run.id
            task_name = task.name
        finally:
            session.close()

        process = self._adapter.start(arguments)
        live = LiveRun(
            run_id=run_id,
            task_id=task_id,
            task_name=task_name,
            dry_run=dry_run,
            started_at=datetime.now(timezone.utc),
            process=process,
        )
        with self._lock:
            self._runs[run_id] = live

        thread = threading.Thread(
            target=self._pump, args=(live,), name=f"csm-run-{run_id[:8]}", daemon=True
        )
        thread.start()
        return run_id

    def stop(self, run_id: str, *, grace: float = 15.0) -> bool:
        """Interrompt proprement une exécution (TASK-002, §8.5)."""
        live = self.get(run_id)
        if live is None or live.status != "running":
            return False

        live.cancelled = True
        process = live.process
        try:
            if os.name == "posix":
                # SIGINT : rclone termine les transferts en vol puis sort.
                process.send_signal(signal.SIGINT)
            else:
                process.terminate()
        except (OSError, ValueError):  # pragma: no cover - course à l'arrêt
            return False

        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            process.kill()
        return True

    def stop_all(self) -> None:
        for run_id in [run.run_id for run in self.snapshots_live()]:
            self.stop(run_id, grace=5.0)

    def snapshots_live(self) -> list[LiveRun]:
        with self._lock:
            return [run for run in self._runs.values() if run.status == "running"]

    # -- boucle de lecture --------------------------------------------------

    def _pump(self, live: LiveRun) -> None:
        buffer: list[TaskEvent] = []
        stored = 0
        session = self._session_factory()
        try:
            stream = live.process.stderr
            if stream is not None:
                for line in stream:
                    event = rclone_events.parse_line(line)
                    if event is None:
                        continue
                    stored = self._handle(live, event, buffer, stored, session)
                    if len(buffer) >= 100:
                        session.add_all(buffer)
                        session.commit()
                        buffer.clear()

            exit_code = live.process.wait()
            if buffer:
                session.add_all(buffer)
                buffer.clear()
            self._finalise(live, exit_code, session)
            session.commit()
        except Exception:  # pragma: no cover - filet de sécurité du thread
            logger.exception("exécution %s interrompue par une erreur interne", live.run_id)
            session.rollback()
            live.status = "error"
        finally:
            session.close()

    def _handle(
        self,
        live: LiveRun,
        event: RcloneEvent,
        buffer: list[TaskEvent],
        stored: int,
        session: Session,
    ) -> int:
        if event.kind == rclone_events.STATS:
            if event.stats:
                live.stats = rclone_events.summarise(event.stats)
            return stored

        if event.kind in (rclone_events.TRANSFER, rclone_events.SKIP_TRANSFER):
            live.counters["transfers"] += 1
            live.current_file = event.path
        elif event.kind in rclone_events.DESTRUCTIVE_KINDS:
            live.counters["deletes"] += 1
        elif event.kind == rclone_events.ERROR:
            live.counters["errors"] += 1
            live.last_error = event.message
        else:
            return stored

        if stored < MAX_STORED_EVENTS:
            buffer.append(
                TaskEvent(
                    run_id=live.run_id,
                    kind=event.kind,
                    path=event.path,
                    size=event.size,
                    message=event.message,
                )
            )
            stored += 1
            if stored == MAX_STORED_EVENTS:
                buffer.append(
                    TaskEvent(
                        run_id=live.run_id,
                        kind="warning",
                        message=(
                            f"détail tronqué au-delà de {MAX_STORED_EVENTS} "
                            "événements ; les compteurs restent exacts"
                        ),
                    )
                )
        return stored

    def _finalise(self, live: LiveRun, exit_code: int, session: Session) -> None:
        status = _final_status(live, exit_code)
        live.status = status

        run = session.get(TaskRun, live.run_id)
        if run is not None:
            run.status = status
            run.exit_code = exit_code
            run.ended_at = datetime.now(timezone.utc)
            run.transferred_files = live.counters["transfers"]
            run.transferred_bytes = int(live.stats.get("bytes", 0))
            run.deleted_files = live.counters["deletes"]
            run.errors_count = max(live.counters["errors"], int(live.stats.get("errors", 0)))
            run.summary_json = _summary_json(live)

            task = session.get(Task, live.task_id)
            if task is not None:
                task.status = RUN_TO_TASK_STATUS.get(status, "ready")


def _operation_for(mode: str) -> str:
    return "sync" if mode == "mirror" else "copy"


def _bandwidth_limit(task: Task) -> str | None:
    """Limite de débit stockée sur la tâche (PERF-002).

    Format rclone : ``1M``, ``500k``, ou ``2M:1M`` pour montant:descendant.
    """
    if not task.bandwidth_json:
        return None
    import json as _json

    try:
        payload = _json.loads(task.bandwidth_json)
    except ValueError:
        return None
    limit = payload.get("limit") if isinstance(payload, dict) else None
    return str(limit) if limit else None


def _final_status(live: LiveRun, exit_code: int) -> str:
    """§8.5 — après un arrêt forcé ou un plantage, jamais « Réussie »."""
    if live.cancelled:
        return "interrupted"
    if exit_code != 0:
        return "error"
    if live.counters["errors"] or int(live.stats.get("errors", 0)):
        return "warning"
    return "success"


def _summary_json(live: LiveRun) -> str:
    import json

    return json.dumps(
        {"stats": live.stats, "counters": live.counters, "last_error": live.last_error},
        ensure_ascii=False,
    )


def _version_or_none(adapter: RcloneAdapter) -> str | None:
    try:
        return adapter.version().version
    except Exception:  # pragma: no cover - dépend de l'environnement
        return None
