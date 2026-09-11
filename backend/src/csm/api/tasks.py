"""Tâches de synchronisation (TASK-001, TASK-002, PLAN-001, LOG-001)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from csm.api.deps import get_session, get_settings_dep
from csm.api.schemas import (
    RunOut,
    TaskCreate,
    TaskEventOut,
    TaskEventSummary,
    TaskOut,
    TaskRunStart,
    TaskUpdate,
)
from csm.config import Settings
from csm.db.models import Task, TaskEvent, TaskRun
from csm.services import tasks as service
from csm.services.runner import TRUNCATION_NOTICE, RunError, RunManager

router = APIRouter(tags=["tâches"])


def get_runner(request: Request) -> RunManager:
    runner = getattr(request.app.state, "run_manager", None)
    if runner is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Moteur rclone indisponible : aucune exécution n'est possible.",
        )
    return runner


def _get_task(session: Session, task_id: str) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tâche introuvable")
    return task


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    request: Request, session: Session = Depends(get_session)
) -> list[TaskOut]:
    runner = getattr(request.app.state, "run_manager", None)
    live = {snap["task_id"]: snap for snap in runner.snapshots()} if runner else {}
    return [
        TaskOut.build(task, live=live.get(task.id))
        for task in session.scalars(select(Task).order_by(Task.name)).all()
    ]


@router.post("/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: TaskCreate,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> TaskOut:
    try:
        task = service.create_task(
            session,
            settings,
            name=payload.name,
            remote_id=payload.remote_id,
            local_path=payload.local_path,
            remote_path=payload.remote_path,
            direction=payload.direction,
            mode=payload.mode,
            schedule=payload.schedule,
            filter_set_id=payload.filter_set_id,
            bandwidth=payload.bandwidth,
        )
    except service.TaskError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return TaskOut.build(task)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(
    task_id: str,
    payload: TaskUpdate,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> TaskOut:
    task = _get_task(session, task_id)
    try:
        service.update_task(
            session,
            settings,
            task,
            name=payload.name,
            local_path=payload.local_path,
            remote_path=payload.remote_path,
            schedule=payload.schedule,
            enabled=payload.enabled,
            filter_set_id=payload.filter_set_id,
            bandwidth=payload.bandwidth,
        )
    except service.TaskError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return TaskOut.build(task)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> None:
    task = _get_task(session, task_id)
    runner = getattr(request.app.state, "run_manager", None)
    if runner and runner.is_running(task.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "cette tâche est en cours d'exécution : arrêtez-la avant de la supprimer",
        )
    session.delete(task)


@router.post("/tasks/{task_id}/run", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
def run_task(
    task_id: str,
    payload: TaskRunStart,
    session: Session = Depends(get_session),
    runner: RunManager = Depends(get_runner),
) -> RunOut:
    _get_task(session, task_id)
    try:
        run_id = runner.start(
            task_id,
            dry_run=payload.dry_run,
            confirm_deletions=payload.confirm_deletions,
            resync=payload.resync,
        )
    except RunError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    session.expire_all()
    run = session.get(TaskRun, run_id)
    if run is None:  # pragma: no cover - la transaction du runner a échoué
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "exécution non créée")
    return RunOut.build(run, live=runner.get(run_id))


@router.post("/runs/{run_id}/stop", response_model=RunOut)
def stop_run(
    run_id: str,
    session: Session = Depends(get_session),
    runner: RunManager = Depends(get_runner),
) -> RunOut:
    run = session.get(TaskRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "exécution introuvable")
    if not runner.stop(run_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "cette exécution n'est plus en cours"
        )
    session.expire_all()
    run = session.get(TaskRun, run_id)
    return RunOut.build(run, live=runner.get(run_id))


@router.get("/tasks/{task_id}/runs", response_model=list[RunOut])
def list_runs(
    task_id: str,
    limit: int = 50,
    session: Session = Depends(get_session),
) -> list[RunOut]:
    _get_task(session, task_id)
    runs = session.scalars(
        select(TaskRun)
        .where(TaskRun.task_id == task_id)
        .order_by(TaskRun.started_at.desc())
        .limit(min(limit, 200))
    ).all()
    return [RunOut.build(run) for run in runs]


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(
    run_id: str,
    session: Session = Depends(get_session),
    runner: RunManager = Depends(get_runner),
) -> RunOut:
    run = session.get(TaskRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "exécution introuvable")
    return RunOut.build(run, live=runner.get(run_id))


def _path_filter(q: str) -> Any:
    """Recherche par sous-chaîne de chemin (LOG-002).

    ``%`` et ``_`` sont échappés : saisis dans le champ de recherche, ce sont
    des caractères ordinaires d'un nom de fichier, pas des jokers.

    ``ilike`` reste insensible à la casse sur l'ASCII seulement — SQLite ne
    replie pas les accents. « é » trouve bien « é », mais pas « É ».
    """
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return TaskEvent.path.ilike(f"%{escaped}%", escape="\\")


@router.get("/runs/{run_id}/events", response_model=list[TaskEventOut])
def list_events(
    run_id: str,
    kind: str | None = None,
    q: str | None = None,
    offset: int = 0,
    limit: int = 500,
    session: Session = Depends(get_session),
) -> list[TaskEvent]:
    if session.get(TaskRun, run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "exécution introuvable")
    statement = select(TaskEvent).where(TaskEvent.run_id == run_id)
    if kind:
        statement = statement.where(TaskEvent.kind == kind)
    if q:
        statement = statement.where(_path_filter(q))
    statement = statement.order_by(TaskEvent.id).offset(max(offset, 0))
    return list(session.scalars(statement.limit(min(limit, 2000))).all())


@router.get("/runs/{run_id}/events/summary", response_model=TaskEventSummary)
def summarise_events(
    run_id: str,
    q: str | None = None,
    session: Session = Depends(get_session),
) -> TaskEventSummary:
    """Cardinalité par type, et aveu de troncature (LOG-002, §14).

    Les compteurs suivent la recherche mais ignorent le filtre de type :
    choisir un type ne doit pas faire bouger les nombres sur lesquels on
    vient de cliquer.
    """
    if session.get(TaskRun, run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "exécution introuvable")

    statement = select(TaskEvent.kind, func.count()).where(TaskEvent.run_id == run_id)
    if q:
        statement = statement.where(_path_filter(q))
    counts = {
        str(kind): int(total)
        for kind, total in session.execute(statement.group_by(TaskEvent.kind)).all()
    }

    truncated = session.scalar(
        select(func.count())
        .select_from(TaskEvent)
        .where(TaskEvent.run_id == run_id)
        .where(TaskEvent.kind == "warning")
        .where(TaskEvent.message.startswith(TRUNCATION_NOTICE))
    )
    return TaskEventSummary(
        counts=counts,
        total=sum(counts.values()),
        truncated=bool(truncated),
    )


@router.get("/stream/runs")
async def stream_runs(request: Request) -> StreamingResponse:
    """Progression temps réel (UI-003).

    SSE plutôt que WebSocket (§5.2) : le flux est unidirectionnel, et une
    simple reconnexion HTTP suffit à reprendre après une coupure.
    """
    runner = getattr(request.app.state, "run_manager", None)

    async def publish() -> Any:
        while True:
            if await request.is_disconnected():
                break
            payload = runner.snapshots() if runner else []
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(
        publish(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
