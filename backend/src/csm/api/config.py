"""Filtres, paramètres et sauvegarde de configuration.

FILT-001 → FILT-005, NOTIF-001 → NOTIF-004, DATA-002, DATA-003, DATA-005.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from csm.api.deps import get_adapter, get_session, get_settings_dep
from csm.config import Settings
from csm.db.models import FilterSet, Task
from csm.rclone.adapter import RcloneAdapter, RcloneError
from csm.services import backup, settings_store
from csm.services.filters import (
    FilterError,
    compile_filters,
    explain,
    parse_rules,
)
from csm.services.notifications import EVENTS, Notifier

router = APIRouter(tags=["configuration"])


# -- filtres -----------------------------------------------------------------


class FilterSetIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    rules: list[dict[str, Any]] = Field(default_factory=list)


class FilterSetOut(BaseModel):
    id: str
    name: str
    rules: list[dict[str, Any]]
    #: Le jeu de règles tel que rclone le recevra, à titre de vérification.
    compiled: list[str]
    task_count: int = 0


class FilterPreviewIn(BaseModel):
    path: str = Field(min_length=1)
    max_depth: int = 5


class FilterPreviewOut(BaseModel):
    included: list[str]
    excluded: list[dict[str, str]]
    truncated: bool = False


def _shape(session: Session, item: FilterSet) -> FilterSetOut:
    rules = json.loads(item.rules_json or "[]")
    compiled = compile_filters(parse_rules(rules))
    count = len(
        session.scalars(select(Task).where(Task.filter_set_id == item.id)).all()
    )
    return FilterSetOut(
        id=item.id,
        name=item.name,
        rules=rules,
        compiled=list(compiled.lines) + list(compiled.flags),
        task_count=count,
    )


@router.get("/filter-sets", response_model=list[FilterSetOut])
def list_filter_sets(session: Session = Depends(get_session)) -> list[FilterSetOut]:
    return [
        _shape(session, item)
        for item in session.scalars(select(FilterSet).order_by(FilterSet.name)).all()
    ]


@router.post(
    "/filter-sets", response_model=FilterSetOut, status_code=status.HTTP_201_CREATED
)
def create_filter_set(
    payload: FilterSetIn, session: Session = Depends(get_session)
) -> FilterSetOut:
    if session.scalar(select(FilterSet).where(FilterSet.name == payload.name)):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"un jeu nommé « {payload.name} » existe déjà"
        )
    try:
        parse_rules(payload.rules)
    except FilterError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    item = FilterSet(name=payload.name, rules_json=json.dumps(payload.rules))
    session.add(item)
    session.flush()
    return _shape(session, item)


@router.patch("/filter-sets/{filter_set_id}", response_model=FilterSetOut)
def update_filter_set(
    filter_set_id: str,
    payload: FilterSetIn,
    session: Session = Depends(get_session),
) -> FilterSetOut:
    item = _get(session, filter_set_id)
    try:
        parse_rules(payload.rules)
    except FilterError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    item.name = payload.name
    item.rules_json = json.dumps(payload.rules)
    session.flush()
    return _shape(session, item)


@router.delete("/filter-sets/{filter_set_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_filter_set(
    filter_set_id: str, session: Session = Depends(get_session)
) -> None:
    item = _get(session, filter_set_id)
    linked = session.scalar(
        select(Task).where(Task.filter_set_id == item.id).limit(1)
    )
    if linked is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"ce jeu est utilisé par la tâche « {linked.name} »",
        )
    session.delete(item)


@router.post("/filter-sets/{filter_set_id}/preview", response_model=FilterPreviewOut)
def preview_filter_set(
    filter_set_id: str,
    payload: FilterPreviewIn,
    session: Session = Depends(get_session),
    adapter: RcloneAdapter = Depends(get_adapter),
) -> FilterPreviewOut:
    """Outil de test des filtres (FILT-005).

    L'autorité est **rclone** : on compare le listing filtré au listing
    complet. L'explication associée à chaque exclusion est reconstituée par
    nos soins, à titre indicatif — elle ne décide de rien.
    """
    item = _get(session, filter_set_id)
    rules = parse_rules(json.loads(item.rules_json or "[]"))
    compiled = compile_filters(rules)

    try:
        everything = adapter.lsjson(
            payload.path, max_depth=payload.max_depth, extra=["--files-only"]
        )
        if compiled.empty:
            kept = everything
        else:
            with tempfile.TemporaryDirectory() as directory:
                rules_file = Path(directory) / "preview.filter"
                rules_file.write_text(compiled.as_text(), encoding="utf-8")
                kept = adapter.lsjson(
                    payload.path,
                    max_depth=payload.max_depth,
                    filter_file=str(rules_file),
                    extra=["--files-only", *compiled.flags],
                )
    except RcloneError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    kept_paths = {str(entry.get("Path")) for entry in kept}
    all_paths = [str(entry.get("Path")) for entry in everything]

    excluded = [
        {"path": path, "reason": explain(path, rules)[1]}
        for path in all_paths
        if path not in kept_paths
    ]
    return FilterPreviewOut(
        included=sorted(kept_paths)[:500],
        excluded=excluded[:500],
        truncated=len(all_paths) > 500,
    )


def _get(session: Session, filter_set_id: str) -> FilterSet:
    item = session.get(FilterSet, filter_set_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "jeu de filtres introuvable")
    return item


# -- paramètres --------------------------------------------------------------


class NotificationsIn(BaseModel):
    unraid_url: str | None = None
    #: Absent = inchangé ; chaîne vide = effacement explicite. L'utilisateur
    #: peut donc enregistrer le formulaire sans retaper sa clé (§6.1).
    unraid_api_key: str | None = None
    webhook_url: str | None = None
    events: list[str] | None = None
    allow_self_signed: bool | None = None


@router.get("/settings")
def read_settings(session: Session = Depends(get_session)) -> dict[str, Any]:
    return {"notifications": settings_store.public_settings(session), "events": list(EVENTS)}


@router.put("/settings")
def write_settings(
    payload: NotificationsIn, session: Session = Depends(get_session)
) -> dict[str, Any]:
    try:
        settings_store.update_notifications(
            session,
            unraid_url=payload.unraid_url,
            unraid_api_key=payload.unraid_api_key,
            webhook_url=payload.webhook_url,
            events=payload.events,
            allow_self_signed=payload.allow_self_signed,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.flush()
    return {"notifications": settings_store.public_settings(session)}


@router.post("/settings/notifications/test")
def test_notifications(session: Session = Depends(get_session)) -> dict[str, Any]:
    config = settings_store.notification_config(session)
    if not config.configured:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "aucun canal de notification n'est configuré",
        )
    results = Notifier(config).notify(
        "failure",
        "Test depuis Cloud Sync Manager",
        "Ceci est une notification de test. Aucune tâche n'est concernée.",
    )
    delivered = any(result.ok for result in results)
    # §27.10 — la cause remonte jusqu'à l'utilisateur. Le faire
    # descendre dans les journaux du conteneur reviendrait à lui
    # demander d'ouvrir un terminal pour un diagnostic courant.
    return {
        "delivered": delivered,
        "detail": " · ".join(result.label for result in results)
        or "aucun canal configuré",
        "results": [
            {"channel": r.channel, "ok": r.ok, "detail": r.detail}
            for r in results
        ],
    }


# -- sauvegarde et restauration ----------------------------------------------


@router.get("/config/export")
def export_configuration(session: Session = Depends(get_session)) -> dict[str, Any]:
    return backup.export_config(session)


@router.post("/config/import")
def import_configuration(
    payload: dict[str, Any] = Body(...),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    try:
        report = backup.import_config(session, settings, payload)
    except backup.BackupError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return report.as_dict()
