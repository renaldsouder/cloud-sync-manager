"""Tableau de bord (UI-001, UI-004, TASK-004, §10.2).

Un seul appel doit suffire à peupler l'écran d'accueil : le §13 demande un
tableau de bord utilisable en moins d'une seconde avec cent tâches, ce qui
exclut d'interroger l'historique tâche par tâche depuis le navigateur.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from csm.api.deps import get_session
from csm.api.schemas import DashboardCounters, DashboardOut, RunOut
from csm.db.models import Task, TaskRun
from csm.services.scheduler import as_utc

router = APIRouter(tags=["tableau de bord"])

#: Correspondance entre l'état d'une tâche et le compteur qu'elle alimente.
COUNTED_STATUSES = ("scheduled", "running", "warning", "error", "blocked", "paused")


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    request: Request, session: Session = Depends(get_session)
) -> DashboardOut:
    tasks = list(session.scalars(select(Task)).all())

    counters = DashboardCounters(total=len(tasks))
    for task in tasks:
        status = "paused" if not task.enabled else task.status
        if status in COUNTED_STATUSES:
            setattr(counters, status, getattr(counters, status) + 1)

    runner = getattr(request.app.state, "run_manager", None)
    live = runner.snapshots() if runner else []
    # Le compteur d'état vient de la base ; une exécution qui vient de
    # démarrer peut ne pas encore y être reflétée.
    counters.running = max(counters.running, len(live))

    upcoming = [
        as_utc(task.next_run_at)
        for task in tasks
        if task.enabled and task.next_run_at is not None
    ]

    recent = session.scalars(
        select(TaskRun).order_by(TaskRun.started_at.desc()).limit(10)
    ).all()

    return DashboardOut(
        counters=counters,
        next_run_at=min(upcoming) if upcoming else None,
        throughput_bytes_per_second=sum(
            float(run.get("stats", {}).get("speed") or 0.0) for run in live
        ),
        running=live,
        recent_runs=[RunOut.build(run) for run in recent],
    )
