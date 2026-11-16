"""Stockages Cloud (CLOUD-001 → CLOUD-006)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from pydantic import BaseModel
from sqlalchemy.orm import Session

from csm.api.deps import get_adapter, get_session, get_store
from csm.api.schemas import (
    ProviderOut,
    RemoteCreate,
    RemoteOut,
    RemoteReauthorizeIn,
    RemoteTestOut,
    RemoteUpdate,
)
from csm.db.models import Remote, Task
from csm.paths import PathNotAllowed, to_remote_path
from csm.rclone import providers as provider_catalog
from csm.rclone.adapter import RcloneAdapter, RcloneError
from csm.rclone.config_store import RcloneConfigStore
from csm.services import remotes as service

router = APIRouter(tags=["stockages"])


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(
    include_all: bool = False,
    include_advanced: bool = False,
    adapter: RcloneAdapter = Depends(get_adapter),
) -> list[dict]:
    """Fournisseurs disponibles.

    Par défaut, seuls les sept mis en avant (§9.1) ; ``include_all`` expose
    tout le catalogue rclone, c'est le mode avancé de CLOUD-004.
    """
    try:
        catalogue = adapter.providers()
    except RcloneError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    shaped = [
        provider_catalog.shape_provider(item, include_advanced=include_advanced)
        for item in catalogue
    ]
    if not include_all:
        shaped = [item for item in shaped if item["priority"]]
    return provider_catalog.sort_providers(shaped)


def _task_counts(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(Task.remote_id, func.count(Task.id))
        .where(Task.one_shot.is_(False))
        .group_by(Task.remote_id)
    ).all()
    return {remote_id: count for remote_id, count in rows}


def _get_remote(session: Session, remote_id: str) -> Remote:
    remote = session.get(Remote, remote_id)
    if remote is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "stockage introuvable")
    return remote


class RemoteEntry(BaseModel):
    name: str
    #: Chemin relatif à la racine du stockage, tel qu'une tâche l'attend.
    path: str


class RemoteListing(BaseModel):
    path: str
    parent: str | None = None
    entries: list[RemoteEntry] = []
    truncated: bool = False


#: Au-delà, la liste cesse d'être utile et l'appel devient lent sur un
#: fournisseur distant.
MAX_REMOTE_ENTRIES = 500


@router.get("/remotes/{remote_id}/browse", response_model=RemoteListing)
def browse_remote(
    remote_id: str,
    path: str = "",
    session: Session = Depends(get_session),
    adapter: RcloneAdapter = Depends(get_adapter),
) -> RemoteListing:
    """Parcours de l'arborescence distante (CLOUD-005, §3).

    Choisir un dossier distant supposait de connaître son nom exact et de le
    taper sans filet. Une faute de frappe ne se voyait qu'à la première
    exécution, sur un dossier créé par erreur.

    Les chemins sont normalisés comme à la création de tâche : une tentative
    de remonter au-dessus de la racine du stockage est refusée, car rien ne
    garantit qu'un fournisseur la traiterait comme un système de fichiers
    local le ferait.
    """
    remote = _get_remote(session, remote_id)
    try:
        normalise = to_remote_path(path)
    except PathNotAllowed as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    cible = f"{remote.rclone_remote_name}:{normalise}"
    try:
        trouves = adapter.lsjson(cible, dirs_only=True, max_depth=1)
    except RcloneError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    noms = sorted(
        (str(item.get("Name", "")) for item in trouves if item.get("Name")),
        key=str.lower,
    )
    # PurePosixPath("") vaut ".", que rclone comprend mais qui ferait
    # afficher un dossier nommé « . » et empêcherait de reconnaître la racine.
    courant = "" if normalise == "." else normalise.strip("/")
    entries = [
        RemoteEntry(name=nom, path=f"{courant}/{nom}" if courant else nom)
        for nom in noms[:MAX_REMOTE_ENTRIES]
    ]

    parent: str | None = None
    if courant:
        parent = courant.rsplit("/", 1)[0] if "/" in courant else ""

    return RemoteListing(
        path=courant,
        parent=parent,
        entries=entries,
        truncated=len(noms) > MAX_REMOTE_ENTRIES,
    )


@router.get("/remotes", response_model=list[RemoteOut])
def list_remotes(
    session: Session = Depends(get_session),
    adapter: RcloneAdapter = Depends(get_adapter),
    store: RcloneConfigStore = Depends(get_store),
) -> list[RemoteOut]:
    counts = _task_counts(session)
    return [
        RemoteOut.build(
            remote,
            options=service.describe_options(adapter, store, remote),
            task_count=counts.get(remote.id, 0),
        )
        for remote in session.scalars(select(Remote).order_by(Remote.name)).all()
    ]


@router.post("/remotes", response_model=RemoteOut, status_code=status.HTTP_201_CREATED)
def create_remote(
    payload: RemoteCreate,
    session: Session = Depends(get_session),
    adapter: RcloneAdapter = Depends(get_adapter),
    store: RcloneConfigStore = Depends(get_store),
) -> RemoteOut:
    try:
        remote = service.create_remote(
            session,
            adapter,
            store,
            name=payload.name,
            provider=payload.provider,
            options=payload.options,
        )
    except service.RemoteError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except RcloneError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return RemoteOut.build(
        remote, options=service.describe_options(adapter, store, remote)
    )


@router.patch("/remotes/{remote_id}", response_model=RemoteOut)
def update_remote(
    remote_id: str,
    payload: RemoteUpdate,
    session: Session = Depends(get_session),
    adapter: RcloneAdapter = Depends(get_adapter),
    store: RcloneConfigStore = Depends(get_store),
) -> RemoteOut:
    remote = _get_remote(session, remote_id)
    try:
        service.update_remote(
            session, adapter, store, remote, name=payload.name, options=payload.options
        )
    except service.RemoteError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    return RemoteOut.build(
        remote, options=service.describe_options(adapter, store, remote)
    )


@router.delete("/remotes/{remote_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_remote(
    remote_id: str,
    session: Session = Depends(get_session),
    store: RcloneConfigStore = Depends(get_store),
) -> None:
    remote = _get_remote(session, remote_id)
    try:
        service.delete_remote(session, store, remote)
    except service.RemoteError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/remotes/{remote_id}/complete")
def complete_remote(
    remote_id: str,
    session: Session = Depends(get_session),
    adapter: RcloneAdapter = Depends(get_adapter),
    store: RcloneConfigStore = Depends(get_store),
) -> dict:
    """Complète une configuration OneDrive à laquelle il manque le disque."""
    remote = _get_remote(session, remote_id)
    try:
        found = service.complete_onedrive(session, adapter, store, remote)
    except service.RemoteError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"options": found}


@router.post("/remotes/{remote_id}/reauthorize")
def reauthorize_remote(
    remote_id: str,
    payload: RemoteReauthorizeIn,
    request: Request,
    session: Session = Depends(get_session),
    store: RcloneConfigStore = Depends(get_store),
) -> dict:
    """Remplace le jeton OAuth d'un stockage, sans toucher au reste."""
    remote = _get_remote(session, remote_id)
    runner = getattr(request.app.state, "run_manager", None)
    running = {
        task.id
        for task in session.scalars(select(Task).where(Task.remote_id == remote.id)).all()
        if runner is not None and runner.is_running(task.id)
    }
    try:
        rearmed = service.reauthorize(
            session,
            store,
            remote,
            token=payload.token,
            drive_id=payload.drive_id,
            drive_type=payload.drive_type,
            running_task_ids=running,
        )
    except service.RemoteError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"simulation_required": rearmed}


@router.post("/remotes/{remote_id}/test", response_model=RemoteTestOut)
def test_remote(
    remote_id: str,
    session: Session = Depends(get_session),
    adapter: RcloneAdapter = Depends(get_adapter),
) -> dict:
    remote = _get_remote(session, remote_id)
    return service.test_remote(session, adapter, remote)
