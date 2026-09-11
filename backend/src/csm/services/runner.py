"""Exécution des tâches (SYNC-001/002/004, TASK-002, UI-003, §8).

Un processus rclone par exécution (décision P3). L'attribution des
événements est alors structurelle : ce qui sort du ``stderr`` de ce
processus appartient à cette exécution, et à aucune autre, même quand
plusieurs tâches tournent en parallèle.

Une exécution destructive se déroule en trois temps :

1. **contrôle de la source** — inaccessible ou anormalement vide, on
   s'arrête sans rien supprimer (§8.2) ;
2. **simulation de contrôle** — on mesure ce qui serait supprimé et on
   compare aux seuils *avant* qu'un fichier ne bouge (§8.3) ;
3. **transfert** — avec quarantaine, donc réversible (CONF-004).
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from csm.db.models import FilterSet, Remote, Task, TaskEvent, TaskRun, new_id
from csm.rclone import events as rclone_events
from csm.rclone.adapter import (
    BISYNC_CHECK_FILENAME,
    BISYNC_NEEDS_RESYNC,
)
from csm.rclone.adapter import RcloneAdapter, RcloneError
from csm.rclone.events import RcloneEvent
from csm.services import guards
from csm.services.tasks import MODES
from csm.services import notifications, settings_store
from csm.services.filters import FilterError, compile_filters, parse_rules
from csm.services.guards import DeletionPlan, GuardBlocked

logger = logging.getLogger("csm.runner")

#: Plafond d'événements fichier par fichier conservés en base. Au-delà, les
#: compteurs restent exacts mais le détail est tronqué (§13, LOG-005).
MAX_STORED_EVENTS = 5000

#: Début du message d'avertissement écrit quand le détail est tronqué.
#: La vue de détail (LOG-002) s'en sert pour signaler une liste incomplète :
#: une liste tronquée qui paraîtrait complète ferait conclure à tort qu'un
#: fichier n'a pas été transféré.
TRUNCATION_NOTICE = "détail tronqué"

SUPPORTED_MODES = frozenset({"copy", "mirror"})

RUN_TO_TASK_STATUS = {
    "needs_resync": "needs_resync",
    "success": "success",
    "warning": "warning",
    "error": "error",
    "interrupted": "ready",
    "blocked": "blocked",
}


#: Réglages par défaut du bidirectionnel. ``initialised`` est faux tant
#: qu'une ré-initialisation explicite n'a pas établi la référence : bisync
#: refuse de tourner sans elle, et c'est une protection, pas une gêne.
BISYNC_DEFAULTS: dict[str, Any] = {
    "initialised": False,
    "initialised_at": None,
    "resync_simulated": False,
    "conflict_resolve": "none",
    "conflict_loser": "num",
    "check_access": False,
}


def bisync_settings(task: Task) -> dict[str, Any]:
    """Réglages bidirectionnels d'une tâche, complétés par les défauts."""
    stored: dict[str, Any] = {}
    if task.bisync_json:
        try:
            loaded = json.loads(task.bisync_json)
            if isinstance(loaded, dict):
                stored = loaded
        except json.JSONDecodeError:
            logger.warning("réglages bisync illisibles sur « %s »", task.name)
    return {**BISYNC_DEFAULTS, **stored}


def store_bisync_settings(task: Task, **changes: Any) -> dict[str, Any]:
    settings = {**bisync_settings(task), **changes}
    task.bisync_json = json.dumps(settings)
    return settings


class RunError(RuntimeError):
    """Refus d'exécuter, message destiné à l'utilisateur."""


@dataclass
class RunPlan:
    operation: str
    source: str
    destination: str
    dry_run: bool
    confirm_deletions: bool = False
    bwlimit: str | None = None
    transfers: int | None = None
    checkers: int | None = None
    filter_file: str | None = None
    max_deletes: int | None = None
    max_delete_percent: int | None = None
    quarantine: bool = True

    # -- bidirectionnel (SYNC-003) ------------------------------------------
    #: Répertoire des listings de bisync. Obligatoire pour ce mode : son
    #: emplacement par défaut ne survit pas au conteneur.
    workdir: str | None = None
    #: Ré-initialisation. Jamais implicite (§7.3).
    resync: bool = False
    conflict_resolve: str = "none"
    conflict_loser: str = "num"
    check_access: bool = False

    @property
    def destructive(self) -> bool:
        """Une simulation ne détruit rien, une Copie non plus (§7.1).

        Le bidirectionnel l'est toujours : il peut supprimer des deux côtés,
        et une ré-initialisation fusionne les deux arborescences.
        """
        return self.operation in {"sync", "bisync"} and not self.dry_run


@dataclass
class LiveRun:
    run_id: str
    task_id: str
    task_name: str
    dry_run: bool
    started_at: datetime
    resync: bool = False
    process: subprocess.Popen[str] | None = None
    phase: str = "démarrage"
    status: str = "running"
    cancelled: bool = False
    current_file: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    counters: dict[str, int] = field(
        default_factory=lambda: {
            "transfers": 0,
            "deletes": 0,
            "errors": 0,
            "conflicts": 0,
        }
    )
    last_error: str | None = None
    blocked_reason: str | None = None
    deletion_plan: DeletionPlan | None = None

    def snapshot(self) -> dict[str, Any]:
        plan = self.deletion_plan
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "task_name": self.task_name,
            "dry_run": self.dry_run,
            "status": self.status,
            "phase": self.phase,
            "started_at": self.started_at.isoformat(),
            "current_file": self.current_file,
            "counters": dict(self.counters),
            "stats": dict(self.stats),
            "last_error": self.last_error,
            "blocked_reason": self.blocked_reason,
            "deletion_plan": (
                {
                    "deletes": plan.deletes,
                    "checks": plan.checks,
                    "percent": plan.percent,
                    "paths": plan.paths[:50],
                }
                if plan
                else None
            ),
        }


def endpoints(task: Task, remote: Remote) -> tuple[str, str]:
    """Source et destination, dans l'ordre attendu par rclone."""
    remote_spec = f"{remote.rclone_remote_name}:{task.remote_path}"
    if task.direction == "local_to_remote":
        return task.local_path, remote_spec
    return remote_spec, task.local_path


def requires_dry_run(task: Task) -> bool:
    """§8.1 — simulation exigée avant la première exécution destructive.

    Une Copie ne supprime rien : la lui imposer n'apporterait aucune sécurité
    et découragerait l'usage du garde-fou là où il compte vraiment.
    """
    return task.dry_run_required and task.mode != "copy"


class RunManager:
    """Registre des exécutions en cours."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        adapter: RcloneAdapter,
        *,
        quarantine_retention_days: int = 30,
        quarantine_keep_runs: int = 3,
        filters_dir: Path | None = None,
        bisync_dir: Path | None = None,
        bidirectional_enabled: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._adapter = adapter
        self._filters_dir = filters_dir
        self._bisync_dir = bisync_dir
        self._bidirectional_enabled = bidirectional_enabled
        self._retention_days = quarantine_retention_days
        self._keep_runs = quarantine_keep_runs
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

    def _forget(self, run_id: str) -> None:
        """Retire une exécution terminée du registre.

        Sans cela, le registre grossit indéfiniment (§13) et — plus visible —
        une tâche terminée continue d'être annoncée « en cours » par le flux
        de progression, avec un bouton « Arrêter » qui ne sert plus à rien.
        """
        with self._lock:
            self._runs.pop(run_id, None)

    def live_runs(self) -> list[LiveRun]:
        with self._lock:
            return [run for run in self._runs.values() if run.status == "running"]

    # -- cycle de vie -------------------------------------------------------

    def _bisync_arguments(self, plan: RunPlan) -> list[str]:
        """Arguments d'une exécution bidirectionnelle.

        La corbeille est demandée des deux côtés : en bidirectionnel, une
        suppression peut frapper le local comme le distant, et n'en protéger
        qu'un seul reviendrait à n'en protéger aucun.
        """
        assert plan.workdir is not None  # garanti par _workdir_for
        backup1 = backup2 = None
        if plan.destructive and plan.quarantine:
            backup1 = guards.quarantine_directory(plan.source)
            backup2 = guards.quarantine_directory(plan.destination)

        return self._adapter.build_bisync_args(
            plan.source,
            plan.destination,
            plan.workdir,
            dry_run=plan.dry_run,
            resync=plan.resync,
            check_access=plan.check_access,
            conflict_resolve=plan.conflict_resolve,
            conflict_loser=plan.conflict_loser,
            max_delete=plan.max_deletes,
            backup_dir1=backup1,
            backup_dir2=backup2,
            filter_file=plan.filter_file,
            transfers=plan.transfers,
            checkers=plan.checkers,
            bwlimit=plan.bwlimit,
        )

    def start(
        self,
        task_id: str,
        *,
        dry_run: bool,
        confirm_deletions: bool = False,
        resync: bool = False,
    ) -> str:
        if self.is_running(task_id):
            raise RunError("cette tâche est déjà en cours d'exécution")

        session = self._session_factory()
        try:
            task = session.get(Task, task_id)
            if task is None:
                raise RunError("tâche introuvable")
            self._check_mode(task)
            if task.mode == "bisync":
                self._check_bisync_protocol(task, dry_run=dry_run, resync=resync)
            elif resync:
                raise RunError(
                    "la ré-initialisation ne concerne que les tâches "
                    "bidirectionnelles"
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
            performance = _performance(task)
            bisync = bisync_settings(task) if task.mode == "bisync" else {}
            plan = RunPlan(
                operation=_operation_of(task),
                source=source,
                destination=destination,
                dry_run=dry_run,
                confirm_deletions=confirm_deletions,
                bwlimit=performance.get("limit"),
                transfers=performance.get("transfers"),
                checkers=performance.get("checkers"),
                filter_file=_write_filter_file(session, task, self._filters_dir),
                max_deletes=task.max_deletes,
                max_delete_percent=task.max_delete_percent,
                quarantine=task.quarantine_enabled,
                workdir=self._workdir_for(task) if task.mode == "bisync" else None,
                resync=resync,
                conflict_resolve=bisync.get("conflict_resolve", "none"),
                conflict_loser=bisync.get("conflict_loser", "num"),
                check_access=bool(bisync.get("check_access", False)),
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
            live = LiveRun(
                run_id=run.id,
                task_id=task_id,
                task_name=task.name,
                dry_run=dry_run,
                started_at=datetime.now(timezone.utc),
                resync=resync,
            )
        finally:
            session.close()

        with self._lock:
            self._runs[live.run_id] = live

        threading.Thread(
            target=self._execute,
            args=(live, plan),
            name=f"csm-run-{live.run_id[:8]}",
            daemon=True,
        ).start()
        return live.run_id

    def stop(self, run_id: str, *, grace: float = 15.0) -> bool:
        """Interrompt proprement une exécution (TASK-002, §8.5)."""
        live = self.get(run_id)
        if live is None or live.status != "running":
            return False

        live.cancelled = True
        process = live.process
        if process is None:
            # Arrêt demandé entre deux phases, ou avant que le processus ne
            # soit enregistré : _spawn honorera l'annulation en attente.
            return True

        return _terminate(process, grace)

    def stop_all(self) -> None:
        for run in self.live_runs():
            self.stop(run.run_id, grace=5.0)

    # -- déroulé ------------------------------------------------------------

    def _execute(self, live: LiveRun, plan: RunPlan) -> None:
        session = self._session_factory()
        try:
            if plan.destructive:
                live.phase = "contrôle de la source"
                try:
                    guards.check_source_available(
                        self._adapter, plan.source, plan.destination
                    )
                except GuardBlocked as exc:
                    self._finalise_blocked(live, session, str(exc))
                    session.commit()
                    self._notify(live, 'blocked', str(exc))
                    return

                if live.cancelled:
                    self._finalise(live, None, session)
                    session.commit()
                    return

                live.phase = "simulation de contrôle"
                deletion_plan = self._simulate(live, plan)
                live.deletion_plan = deletion_plan

                if live.cancelled:
                    self._finalise(live, None, session)
                    session.commit()
                    return

                reason = (
                    None
                    if plan.confirm_deletions
                    else guards.evaluate_threshold(
                        deletion_plan,
                        max_deletes=plan.max_deletes,
                        max_delete_percent=plan.max_delete_percent,
                    )
                )
                if reason:
                    self._store_planned_deletions(live, session, deletion_plan)
                    self._finalise_blocked(live, session, reason)
                    session.commit()
                    self._notify(live, 'blocked', reason)
                    return

            live.phase = "transfert"
            exit_code = self._pump(live, session, self._arguments(live, plan))
            self._finalise(live, exit_code, session)

            if plan.destructive and live.status in {"success", "warning"}:
                live.phase = "purge de la quarantaine"
                self._purge_quarantine(live, plan, session)

            session.commit()
            self._notify(live, live.status, live.last_error)
        except Exception:  # pragma: no cover - filet de sécurité du fil
            logger.exception("exécution %s interrompue par une erreur interne", live.run_id)
            session.rollback()
            live.status = "error"
        finally:
            session.close()
            self._forget(live.run_id)

    # -- bidirectionnel -----------------------------------------------------

    def _check_mode(self, task: Task) -> None:
        if task.mode not in MODES:
            raise RunError(f"mode inconnu : {task.mode}")
        if task.mode == "bisync" and not self._bidirectional_enabled:
            raise RunError(
                "le mode bidirectionnel n'est pas encore disponible : sa "
                "matrice de tests destructifs n'est pas complète"
            )
        if task.mode not in SUPPORTED_MODES and task.mode != "bisync":
            raise RunError(f"le mode « {task.mode} » n'est pas encore disponible")

    def _check_bisync_protocol(
        self, task: Task, *, dry_run: bool, resync: bool
    ) -> None:
        """Protocole d'initialisation (§7.3, §8.1).

        bisync ne sait rien comparer tant qu'une ré-initialisation n'a pas
        établi la référence. Celle-ci fusionne les deux côtés : elle ne doit
        jamais partir toute seule, et elle exige d'avoir été simulée d'abord.
        """
        settings = bisync_settings(task)
        if resync:
            if not dry_run and not settings.get("resync_simulated"):
                raise RunError(
                    "simulez la ré-initialisation avant de l'appliquer : elle "
                    "fusionne les deux côtés et le détail de ce qu'elle ferait "
                    "doit être lu d'abord"
                )
            return
        if not settings.get("initialised"):
            raise RunError(
                "cette tâche bidirectionnelle n'a pas encore été initialisée : "
                "lancez la ré-initialisation, qui établit la référence commune "
                "aux deux côtés"
            )

    def place_access_markers(self, task_id: str) -> dict[str, str]:
        """Dépose les témoins de ``--check-access`` des deux côtés (§8.6).

        La vérification d'accès ne vaut que si les deux témoins existent
        vraiment. Les poser est une action explicite : les créer en douce au
        premier lancement viderait le garde-fou de son sens, puisqu'un côté
        inaccessible se verrait doter d'un témoin dès qu'il redeviendrait
        accessible.
        """
        session = self._session_factory()
        try:
            task = session.get(Task, task_id)
            if task is None:
                raise RunError("tâche introuvable")
            if task.mode != "bisync":
                raise RunError(
                    "les témoins d'accès ne concernent que les tâches "
                    "bidirectionnelles"
                )
            remote = session.get(Remote, task.remote_id)
            if remote is None:
                raise RunError("le stockage associé à cette tâche a disparu")
            source, destination = endpoints(task, remote)
        finally:
            session.close()

        poses: dict[str, str] = {}
        for label, racine in (("path1", source), ("path2", destination)):
            cible = f"{racine.rstrip('/')}/{BISYNC_CHECK_FILENAME}"
            try:
                self._adapter.touch(cible)
            except RcloneError as exc:
                raise RunError(
                    f"témoin impossible à déposer côté {label} : {exc}"
                ) from exc
            poses[label] = cible
        return poses

    def _workdir_for(self, task: Task) -> str:
        """Répertoire des listings, sous l'appdata.

        Leur perte force une ré-initialisation, donc une fusion : ils doivent
        survivre à la recréation du conteneur, pas vivre dans un cache.
        """
        if self._bisync_dir is None:
            raise RunError(
                "aucun répertoire de travail n'est configuré pour le "
                "bidirectionnel"
            )
        workdir = Path(self._bisync_dir) / task.id
        workdir.mkdir(parents=True, exist_ok=True)
        return str(workdir)

    def _arguments(self, live: LiveRun, plan: RunPlan) -> list[str]:
        if plan.operation == "bisync":
            return self._bisync_arguments(plan)

        backup_dir: str | None = None
        extra: list[str] = []

        if plan.destructive and plan.quarantine:
            backup_dir = guards.quarantine_directory(plan.destination)
            # Sans cette exclusion, rclone refuse le chevauchement entre la
            # destination et la corbeille qu'elle contient.
            extra += ["--exclude", guards.QUARANTINE_EXCLUDE]

        if plan.destructive:
            belt = (
                (live.deletion_plan.deletes if live.deletion_plan else None)
                if plan.confirm_deletions
                else plan.max_deletes
            )
            if belt is not None:
                # Seconde ceinture seulement : rclone supprime jusqu'au seuil
                # avant d'abandonner, la décision de bloquer a déjà été prise.
                extra += ["--max-delete", str(belt)]

        return self._adapter.build_transfer_args(
            plan.operation,
            plan.source,
            plan.destination,
            dry_run=plan.dry_run,
            bwlimit=plan.bwlimit,
            transfers=plan.transfers,
            checkers=plan.checkers,
            backup_dir=backup_dir,
            filter_file=plan.filter_file,
            extra=extra,
        )

    def _spawn(self, live: LiveRun, arguments: list[str]) -> subprocess.Popen[str]:
        """Démarre rclone en honorant une annulation déjà demandée.

        Sans ce contrôle, un arrêt tombant entre le démarrage du processus et
        son enregistrement passerait inaperçu : ``stop`` ne trouverait rien à
        interrompre et le transfert irait à son terme malgré la demande.
        """
        process = self._adapter.start(arguments)
        live.process = process
        if live.cancelled:
            _terminate(process, 5.0)
        return process

    def _simulate(self, live: LiveRun, plan: RunPlan) -> DeletionPlan:
        """Mesure ce que ferait l'exécution réelle, sans rien écrire.

        Les arguments sont construits par le même aiguillage que l'exécution :
        mesurer avec une commande différente de celle qui agira ne prouverait
        rien (§20.3).
        """
        arguments = self._simulation_arguments(plan)
        result = DeletionPlan()
        process = self._spawn(live, arguments)
        try:
            stream = process.stderr
            if stream is not None:
                for line in stream:
                    event = rclone_events.parse_line(line)
                    if event is None:
                        continue
                    if event.kind == rclone_events.STATS and event.stats:
                        summary = rclone_events.summarise(event.stats)
                        result.deletes = summary["deletes"]
                        result.checks = summary["checks"]
                        result.transfers = summary["transfers"]
                    elif event.kind == rclone_events.SKIP_DELETE and event.path:
                        if len(result.paths) < guards.MAX_LISTED_PATHS:
                            result.paths.append(event.path)
            process.wait()
        finally:
            live.process = None
        # Le décompte des chemins fait foi sur celui des statistiques, qui
        # peut manquer le dernier relevé si le processus se termine vite.
        result.deletes = max(result.deletes, len(result.paths))
        return result

    def _simulation_arguments(self, plan: RunPlan) -> list[str]:
        if plan.operation == "bisync":
            simulated = replace(plan, dry_run=True)
            return self._bisync_arguments(simulated)
        return self._adapter.build_transfer_args(
            plan.operation,
            plan.source,
            plan.destination,
            dry_run=True,
            bwlimit=plan.bwlimit,
            transfers=plan.transfers,
            checkers=plan.checkers,
            filter_file=plan.filter_file,
            # La corbeille doit être exclue ici aussi : sans cela, la
            # simulation compterait comme « à supprimer » tout ce qu'une
            # exécution précédente y a déposé, et ferait franchir le seuil
            # à une tâche qui n'a pourtant rien de dangereux.
            extra=["--exclude", guards.QUARANTINE_EXCLUDE] if plan.quarantine else None,
        )

    def _pump(self, live: LiveRun, session: Session, arguments: list[str]) -> int:
        buffer: list[TaskEvent] = []
        stored = 0
        process = self._spawn(live, arguments)
        try:
            stream = process.stderr
            if stream is not None:
                for line in stream:
                    event = rclone_events.parse_line(line)
                    if event is None:
                        continue
                    stored = self._handle(live, event, buffer, stored)
                    if len(buffer) >= 100:
                        session.add_all(buffer)
                        session.commit()
                        buffer.clear()
            exit_code = process.wait()
        finally:
            live.process = None

        if buffer:
            session.add_all(buffer)
        return exit_code

    def _handle(
        self,
        live: LiveRun,
        event: RcloneEvent,
        buffer: list[TaskEvent],
        stored: int,
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
        elif event.kind == rclone_events.CONFLICT:
            # Rien n'est perdu, mais le nom d'origine a disparu au profit des
            # deux copies suffixées : c'est un événement à conserver, pas un
            # détail mécanique.
            live.counters["conflicts"] += 1
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
                            f"{TRUNCATION_NOTICE} au-delà de {MAX_STORED_EVENTS} "
                            "événements ; les compteurs restent exacts"
                        ),
                    )
                )
        return stored

    def _notify(self, live: LiveRun, status: str, detail: str | None) -> None:
        """Alerte l'utilisateur d'une exécution qui demande une action (§15).

        Après la validation en base et dans une session à part : une
        notification qui traîne ne doit pas retenir une transaction, et un
        canal injoignable ne doit jamais faire échouer une synchronisation.
        """
        event = notifications.event_for(status)
        if event is None:
            return

        session = self._session_factory()
        try:
            config = settings_store.notification_config(session)
            if not config.configured or not config.wants(event):
                return
            subject, description = notifications.summarise_run(
                live.task_name, status, detail
            )
            notifications.Notifier(config).notify(event, subject, description)
        except Exception:  # pragma: no cover - une alerte n'échoue jamais bruyamment
            logger.warning("notification non envoyée", exc_info=True)
        finally:
            session.close()

    def _purge_quarantine(self, live: LiveRun, plan: RunPlan, session: Session) -> None:
        """Applique la rétention aux corbeilles (§16 — pas de croissance illimitée).

        Sans cela, un Miroir actif finirait par remplir la destination avec
        ses propres sauvegardes, ce qui transformerait une protection en
        panne de stockage.
        """
        root = guards.quarantine_root(plan.destination)
        try:
            entries = self._adapter.lsjson(root, dirs_only=True, max_depth=1)
        except RcloneError:
            return  # aucune corbeille à cet endroit : rien à purger

        expired = guards.expired_batches(
            [str(entry.get("Name", "")) for entry in entries],
            now=datetime.now(timezone.utc),
            retention_days=self._retention_days,
            keep_last=self._keep_runs,
        )

        purged = 0
        for name in expired:
            path = f"{root}/{name}"
            # Ceinture et bretelles : purge supprime récursivement, elle ne
            # doit jamais recevoir autre chose qu'une corbeille à nous.
            if not guards.is_quarantine_path(path):
                logger.warning("purge refusée pour un chemin inattendu : %s", path)
                continue
            try:
                self._adapter.purge(path)
                purged += 1
            except RcloneError as exc:
                logger.warning("purge de %s impossible : %s", path, exc)

        if purged:
            session.add(
                TaskEvent(
                    run_id=live.run_id,
                    kind="warning",
                    message=(
                        f"{purged} corbeille(s) de plus de {self._retention_days} "
                        "jours purgée(s)"
                    ),
                )
            )

    # -- clôture ------------------------------------------------------------

    def _store_planned_deletions(
        self, live: LiveRun, session: Session, plan: DeletionPlan
    ) -> None:
        """§8.4 — la liste de ce qui aurait été supprimé reste tracée.

        C'est elle qui alimente l'écran « Supprimer 423 fichiers » du §10.4 :
        l'utilisateur doit voir *quoi*, pas seulement *combien*.
        """
        session.add_all(
            TaskEvent(
                run_id=live.run_id,
                kind=rclone_events.SKIP_DELETE,
                path=path,
                message="suppression prévue, bloquée par le seuil",
            )
            for path in plan.paths
        )

    def _finalise_blocked(self, live: LiveRun, session: Session, reason: str) -> None:
        live.status = "blocked"
        live.blocked_reason = reason

        session.add(
            TaskEvent(run_id=live.run_id, kind="warning", message=reason)
        )

        run = session.get(TaskRun, live.run_id)
        if run is not None:
            run.status = "blocked"
            run.ended_at = datetime.now(timezone.utc)
            run.deleted_files = 0
            run.summary_json = json.dumps(
                {
                    "blocked_reason": reason,
                    "deletion_plan": (
                        {
                            "deletes": live.deletion_plan.deletes,
                            "checks": live.deletion_plan.checks,
                            "percent": live.deletion_plan.percent,
                        }
                        if live.deletion_plan
                        else None
                    ),
                },
                ensure_ascii=False,
            )
            task = session.get(Task, live.task_id)
            if task is not None:
                task.status = "blocked"

    def _finalise(self, live: LiveRun, exit_code: int | None, session: Session) -> None:
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
            run.errors_count = max(
                live.counters["errors"], int(live.stats.get("errors", 0))
            )
            run.summary_json = _summary_json(live)

            task = session.get(Task, live.task_id)
            if task is not None:
                task.status = RUN_TO_TASK_STATUS.get(status, "ready")
                # §8.1 — la simulation exigée avant la première exécution
                # destructive est consommée dès qu'elle a réussi.
                if live.dry_run and status == "success":
                    task.dry_run_required = False
                if task.mode == "bisync":
                    _record_bisync_outcome(task, live, status)


def mark_orphan_runs_interrupted(session_factory: sessionmaker[Session]) -> int:
    """Rattrape les exécutions coupées par un arrêt du conteneur (§8.5).

    Sans ce passage au démarrage, une exécution tuée en vol resterait
    « en cours » pour toujours : son statut ne serait jamais conclu, et la
    tâche refuserait tout nouveau lancement. Le §8.5 est formel — après un
    arrêt forcé ou un plantage, une exécution est « interrompue », jamais
    « réussie ».
    """
    session = session_factory()
    try:
        orphans = list(
            session.scalars(select(TaskRun).where(TaskRun.status == "running")).all()
        )
        for run in orphans:
            run.status = "interrupted"
            run.ended_at = run.ended_at or datetime.now(timezone.utc)
            task = session.get(Task, run.task_id)
            if task is not None and task.status == "running":
                task.status = "ready"
        if orphans:
            logger.warning(
                "%d exécution(s) interrompue(s) par un arrêt précédent", len(orphans)
            )
            session.commit()
        return len(orphans)
    finally:
        session.close()


def _terminate(process: subprocess.Popen[str], grace: float) -> bool:
    """Arrêt propre puis, si nécessaire, brutal (§8.5)."""
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


def _operation_of(task: Task) -> str:
    return {"mirror": "sync", "bisync": "bisync"}.get(task.mode, "copy")


def _final_status(live: LiveRun, exit_code: int | None) -> str:
    """§8.5 — après un arrêt forcé ou un plantage, jamais « Réussie »."""
    if live.cancelled:
        return "interrupted"
    if exit_code == BISYNC_NEEDS_RESYNC:
        # §27.10 — « erreur » cacherait la seule action qui débloque : bisync
        # ne réclame pas un dépannage mais une ré-initialisation explicite.
        return "needs_resync"
    if exit_code is None or exit_code != 0:
        return "error"
    if live.counters["errors"] or int(live.stats.get("errors", 0)):
        return "warning"
    return "success"


def _record_bisync_outcome(task: Task, live: LiveRun, status: str) -> None:
    """Tient l'état d'initialisation d'une tâche bidirectionnelle.

    Une simulation de ré-initialisation réussie ouvre le droit de l'appliquer
    (§8.1). Une ré-initialisation réelle réussie établit la référence. Le code
    7 dit que les listings ne sont plus exploitables : la tâche redevient non
    initialisée, faute de quoi l'interface proposerait une exécution que
    bisync refuserait.
    """
    if status == "needs_resync":
        store_bisync_settings(task, initialised=False, resync_simulated=False)
        return
    if status != "success" or not live.resync:
        return
    if live.dry_run:
        store_bisync_settings(task, resync_simulated=True)
    else:
        store_bisync_settings(
            task,
            initialised=True,
            initialised_at=datetime.now(timezone.utc).isoformat(),
            resync_simulated=False,
        )


def _summary_json(live: LiveRun) -> str:
    payload: dict[str, Any] = {
        "stats": live.stats,
        "counters": live.counters,
        "last_error": live.last_error,
    }
    if live.deletion_plan is not None:
        payload["deletion_plan"] = {
            "deletes": live.deletion_plan.deletes,
            "checks": live.deletion_plan.checks,
            "percent": live.deletion_plan.percent,
        }
    return json.dumps(payload, ensure_ascii=False)


def _performance(task: Task) -> dict[str, Any]:
    """Réglages de transfert de la tâche (PERF-001, PERF-002).

    ``limit`` suit le format rclone : ``1M``, ``500k``, ou ``2M:1M`` pour
    montant:descendant.
    """
    if not task.bandwidth_json:
        return {}
    try:
        payload = json.loads(task.bandwidth_json)
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}

    settings: dict[str, Any] = {}
    if payload.get("limit"):
        settings["limit"] = str(payload["limit"])
    for key in ("transfers", "checkers"):
        value = payload.get(key)
        if isinstance(value, int) and value > 0:
            settings[key] = value
    return settings


def _write_filter_file(session: Session, task: Task, directory: Path | None) -> str | None:
    """Matérialise le jeu de filtres de la tâche pour ``--filter-from``.

    Le fichier est réécrit à chaque exécution et conservé : il documente
    exactement ce qui a été appliqué, ce dont le diagnostic a besoin (§14).
    """
    # bisync n'accepte pas --exclude et empreinte le contenu de son fichier
    # de filtres. Sans cette exclusion écrite ici, la corbeille d'un côté
    # serait propagée vers l'autre comme un lot de fichiers neufs.
    besoin_corbeille = task.mode == "bisync" and task.quarantine_enabled
    exclusion = f"- {guards.QUARANTINE_EXCLUDE}"

    if not task.filter_set_id or directory is None:
        if not besoin_corbeille or directory is None:
            return None
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{task.id}.filter"
        path.write_text(f"{exclusion}\n", encoding="utf-8")
        return str(path)

    filter_set = session.get(FilterSet, task.filter_set_id)
    if filter_set is None:
        return None

    try:
        compiled = compile_filters(parse_rules(json.loads(filter_set.rules_json or "[]")))
    except (ValueError, FilterError):
        logger.warning("jeu de filtres « %s » illisible, ignoré", filter_set.name)
        return None

    if not compiled.lines:
        return None

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{task.id}.filter"
    corps = compiled.as_text()
    if besoin_corbeille:
        corps = f"{exclusion}\n{corps}"
    path.write_text(corps, encoding="utf-8")
    return str(path)


def _version_or_none(adapter: RcloneAdapter) -> str | None:
    try:
        return adapter.version().version
    except Exception:  # pragma: no cover - dépend de l'environnement
        return None
