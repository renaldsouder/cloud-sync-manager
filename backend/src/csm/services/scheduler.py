"""Planificateur interne (PLAN-002, PLAN-003, LOG-005, §11, §13).

Pas de cron exposé, pas de dépendance à un ordonnanceur externe : une
échéance par tâche en base, et une boucle qui la consulte. Le planificateur
survit ainsi au redémarrage du conteneur sans état en mémoire à reconstruire.

``prime`` et ``tick`` sont volontairement synchrones et prennent leur heure
d'une horloge injectable : c'est ce qui rend la politique de rattrapage du
§11 démontrable au banc d'essai plutôt que déduite d'un réglage.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from csm.db.models import Task, TaskRun
from csm.services.runner import RunError, RunManager
from csm.services.schedule import Schedule, next_occurrence, resolve_after_restart

logger = logging.getLogger("csm.scheduler")

Clock = Callable[[], datetime]


def local_now() -> datetime:
    """Heure locale du conteneur, avec fuseau.

    Les planifications sont exprimées dans l'heure que l'utilisateur lit sur
    son serveur : « tous les jours à 02:30 » doit vouloir dire 02:30 chez lui.
    """
    return datetime.now(timezone.utc).astimezone()


def as_utc(value: datetime | None) -> datetime | None:
    """Normalise une date lue en base.

    SQLite ne conserve pas le fuseau : les dates reviennent naïves alors
    qu'elles ont été écrites en UTC. Sans cette normalisation, toute
    comparaison avec une heure locale serait fausse d'un décalage horaire.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class Scheduler:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        run_manager: RunManager,
        *,
        clock: Clock = local_now,
        poll_seconds: float = 20.0,
        history_retention_days: int = 90,
        history_keep_runs: int = 200,
    ) -> None:
        self._session_factory = session_factory
        self._runs = run_manager
        self._clock = clock
        self._poll_seconds = poll_seconds
        self._retention_days = history_retention_days
        self._keep_runs = history_keep_runs
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- démarrage ----------------------------------------------------------

    def prime(self) -> int:
        """Applique la politique de rattrapage à toutes les tâches (§11).

        Renvoie le nombre d'échéances manquées qui seront rejouées.
        """
        now = self._clock()
        session = self._session_factory()
        caught_up = 0
        try:
            for task in session.scalars(select(Task)).all():
                schedule = Schedule.parse(task.schedule_json)
                stored = as_utc(task.next_run_at)
                resolved, catch_up = resolve_after_restart(schedule, now, stored)
                task.next_run_at = resolved
                if catch_up:
                    caught_up += 1
                    logger.info("rattrapage prévu pour « %s »", task.name)
                if schedule.automatic and task.status == "ready" and resolved:
                    task.status = "scheduled"
            session.commit()
        finally:
            session.close()
        return caught_up

    # -- battement ----------------------------------------------------------

    def tick(self) -> list[str]:
        """Démarre les tâches dues. Renvoie les identifiants d'exécution."""
        now = self._clock()
        started: list[str] = []
        session = self._session_factory()
        try:
            due = [
                task
                for task in session.scalars(
                    select(Task).where(Task.enabled.is_(True))
                ).all()
                if _is_due(task, now)
            ]

            for task in due:
                schedule = Schedule.parse(task.schedule_json)
                # L'échéance est avancée **avant** le lancement : si le
                # démarrage échoue, la tâche ne repart pas en boucle au
                # battement suivant.
                task.next_run_at = next_occurrence(schedule, now)
                session.commit()

                if self._runs.is_running(task.id):
                    # Une exécution précédente déborde sur la suivante. On
                    # saute ce tour plutôt que d'empiler (§11).
                    logger.info(
                        "« %s » déborde sur son échéance suivante, tour ignoré",
                        task.name,
                    )
                    continue

                try:
                    started.append(self._runs.start(task.id, dry_run=False))
                except RunError as exc:
                    logger.warning("« %s » non lancée : %s", task.name, exc)
        finally:
            session.close()
        return started

    # -- rétention ----------------------------------------------------------

    def purge_history(self) -> int:
        """Rétention de l'historique (LOG-005, §13).

        Une exécution n'est supprimée que si elle est plus ancienne que la
        rétention **et** hors des dernières conservées pour sa tâche : un
        support efficace a besoin des derniers résultats, même anciens.
        """
        now = self._clock()
        cutoff = now - timedelta(days=self._retention_days)
        session = self._session_factory()
        removed = 0
        try:
            for task in session.scalars(select(Task)).all():
                runs = list(
                    session.scalars(
                        select(TaskRun)
                        .where(TaskRun.task_id == task.id)
                        .order_by(TaskRun.started_at.desc())
                    ).all()
                )
                for run in runs[self._keep_runs :]:
                    started = as_utc(run.started_at)
                    if started is not None and started < cutoff:
                        session.delete(run)  # les événements suivent en cascade
                        removed += 1
            if removed:
                session.commit()
                logger.info("%d exécution(s) purgée(s) de l'historique", removed)
        finally:
            session.close()
        return removed

    # -- boucle -------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="csm-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5.0)

    def _loop(self) -> None:
        last_purge = self._clock()
        while not self._stop.is_set():
            try:
                self.tick()
                now = self._clock()
                if now - last_purge >= timedelta(hours=24):
                    self.purge_history()
                    last_purge = now
            except Exception:  # pragma: no cover - le battement ne doit jamais mourir
                logger.exception("erreur pendant un battement du planificateur")
            self._stop.wait(self._poll_seconds)


def _is_due(task: Task, now: datetime) -> bool:
    due = as_utc(task.next_run_at)
    return due is not None and due <= now
