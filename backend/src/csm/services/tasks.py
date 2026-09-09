"""Création et validation des tâches (TASK-001, LOCAL-003, LOCAL-005, §8)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from csm.config import Settings
from csm.db.models import Remote, Task
from csm.paths import (
    PathNotAllowed,
    assert_valid_destination,
    is_writable,
    paths_overlap,
    resolve_within_roots,
    to_remote_path,
)

DIRECTIONS = frozenset({"local_to_remote", "remote_to_local"})
MODES = frozenset({"copy", "mirror", "bisync"})

#: Seuils par défaut d'un Miroir (§8.3). Plus stricts en Cloud → Local :
#: la destination est alors le share de l'utilisateur, et une erreur de
#: configuration y détruit des données qu'aucun fournisseur ne reconstruira.
DEFAULT_THRESHOLDS = {
    "local_to_remote": (100, 10),
    "remote_to_local": (25, 5),
}


class TaskError(RuntimeError):
    """Refus de configuration, message destiné à l'utilisateur."""


def validate_local_path(
    session: Session,
    settings: Settings,
    *,
    local_path: str,
    direction: str,
    task_id: str | None = None,
) -> Path:
    """Résout et contrôle le chemin local.

    Le contrôle a lieu **à la création de la tâche**, pas à l'exécution : le
    §10.4 veut que l'utilisateur soit arrêté pendant qu'il configure, pas
    après avoir planifié une exécution nocturne.
    """
    try:
        resolved = resolve_within_roots(local_path, settings.roots)
    except PathNotAllowed as exc:
        raise TaskError(str(exc)) from exc

    if direction == "remote_to_local":
        # Le local devient destination : garde-fous de P13.
        try:
            assert_valid_destination(resolved, settings.roots)
        except PathNotAllowed as exc:
            raise TaskError(str(exc)) from exc
        if not resolved.exists():
            raise TaskError(f"{resolved} n'existe pas")
        if not is_writable(resolved):
            raise TaskError(f"{resolved} n'est pas accessible en écriture")
    elif not resolved.exists():
        raise TaskError(f"{resolved} n'existe pas")

    _refuse_overlap(session, resolved, task_id)
    return resolved


def _refuse_overlap(session: Session, resolved: Path, task_id: str | None) -> None:
    """Deux tâches aux chemins locaux imbriqués peuvent se renvoyer des
    suppressions indéfiniment (§8.6, P13)."""
    statement = select(Task)
    if task_id:
        statement = statement.where(Task.id != task_id)
    for other in session.scalars(statement).all():
        if paths_overlap(resolved, Path(other.local_path)):
            raise TaskError(
                f"le chemin local recoupe celui de la tâche « {other.name} » "
                f"({other.local_path})"
            )


def create_task(
    session: Session,
    settings: Settings,
    *,
    name: str,
    remote_id: str,
    local_path: str,
    remote_path: str,
    direction: str,
    mode: str,
    max_deletes: int | None = None,
    max_delete_percent: int | None = None,
) -> Task:
    if direction not in DIRECTIONS:
        raise TaskError(f"sens inconnu : {direction}")
    if mode not in MODES:
        raise TaskError(f"mode inconnu : {mode}")
    if session.scalar(select(Task).where(Task.name == name)):
        raise TaskError(f"une tâche nommée « {name} » existe déjà")

    remote = session.get(Remote, remote_id)
    if remote is None:
        raise TaskError("stockage introuvable")

    resolved = validate_local_path(
        session, settings, local_path=local_path, direction=direction
    )
    try:
        normalised_remote = to_remote_path(remote_path)
    except PathNotAllowed as exc:
        raise TaskError(str(exc)) from exc

    task = Task(
        name=name,
        remote_id=remote_id,
        local_path=str(resolved),
        remote_path=normalised_remote,
        direction=direction,
        mode=mode,
        # Une Copie ne supprime rien : lui imposer une simulation banaliserait
        # le garde-fou là où il compte vraiment (§8.1).
        dry_run_required=mode != "copy",
        delete_policy="never" if mode == "copy" else "confirm",
        status="ready",
    )
    default_deletes, default_percent = DEFAULT_THRESHOLDS[direction]
    task.max_deletes = max_deletes if max_deletes is not None else default_deletes
    task.max_delete_percent = (
        max_delete_percent if max_delete_percent is not None else default_percent
    )

    session.add(task)
    session.flush()
    return task


def update_task(
    session: Session,
    settings: Settings,
    task: Task,
    *,
    name: str | None = None,
    local_path: str | None = None,
    remote_path: str | None = None,
) -> Task:
    if name and name != task.name:
        if session.scalar(select(Task).where(Task.name == name, Task.id != task.id)):
            raise TaskError(f"une tâche nommée « {name} » existe déjà")
        task.name = name

    if local_path is not None:
        resolved = validate_local_path(
            session,
            settings,
            local_path=local_path,
            direction=task.direction,
            task_id=task.id,
        )
        if str(resolved) != task.local_path:
            task.local_path = str(resolved)
            # §8.1 — changer la source ou la destination réarme la simulation.
            task.dry_run_required = task.mode != "copy"

    if remote_path is not None:
        try:
            normalised = to_remote_path(remote_path)
        except PathNotAllowed as exc:
            raise TaskError(str(exc)) from exc
        if normalised != task.remote_path:
            task.remote_path = normalised
            task.dry_run_required = task.mode != "copy"

    session.flush()
    return task
