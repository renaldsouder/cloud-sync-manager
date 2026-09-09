"""Stockages Cloud (CLOUD-001 → CLOUD-006)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from csm.api.deps import get_adapter, get_session, get_store
from csm.api.schemas import (
    ProviderOut,
    RemoteCreate,
    RemoteOut,
    RemoteTestOut,
    RemoteUpdate,
)
from csm.db.models import Remote, Task
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
        select(Task.remote_id, func.count(Task.id)).group_by(Task.remote_id)
    ).all()
    return {remote_id: count for remote_id, count in rows}


def _get_remote(session: Session, remote_id: str) -> Remote:
    remote = session.get(Remote, remote_id)
    if remote is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "stockage introuvable")
    return remote


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


@router.post("/remotes/{remote_id}/test", response_model=RemoteTestOut)
def test_remote(
    remote_id: str,
    session: Session = Depends(get_session),
    adapter: RcloneAdapter = Depends(get_adapter),
) -> dict:
    remote = _get_remote(session, remote_id)
    return service.test_remote(session, adapter, remote)
