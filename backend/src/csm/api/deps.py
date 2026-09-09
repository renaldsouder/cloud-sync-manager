"""Dépendances FastAPI partagées."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from csm.config import Settings
from csm.rclone.adapter import RcloneAdapter, RcloneUnavailable
from csm.rclone.config_store import RcloneConfigStore


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_session(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_store(settings: Settings = Depends(get_settings_dep)) -> RcloneConfigStore:
    return RcloneConfigStore(settings.rclone_config_path)


def get_adapter(settings: Settings = Depends(get_settings_dep)) -> RcloneAdapter:
    try:
        adapter = RcloneAdapter.from_settings(settings)
    except RcloneUnavailable as exc:
        # 503 et non 500 : le service est correctement configuré, c'est une
        # dépendance externe qui manque. Le message doit le dire clairement.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Moteur rclone indisponible dans le conteneur.",
        ) from exc
    adapter.config_password = settings.rclone_config_password
    return adapter
