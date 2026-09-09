"""Point de santé (§13, UNRAID-001).

Cible du ``HEALTHCHECK`` Docker : Unraid affiche l'état dans son interface.
Répond toujours 200 tant que le processus vit ; ``status`` distingue
``ok`` de ``degraded`` pour que l'utilisateur voie *quoi* est cassé plutôt
qu'un conteneur simplement « unhealthy ».
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import text

from csm import __version__
from csm.rclone.probe import rclone_version

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    checks: dict[str, Any] = {}

    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        checks["database"] = {"ok": False, "detail": "moteur non initialisé"}
    else:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            checks["database"] = {"ok": True}
        except Exception as exc:  # pragma: no cover - dépend de l'environnement
            checks["database"] = {"ok": False, "detail": type(exc).__name__}

    settings = getattr(request.app.state, "settings", None)
    info = rclone_version(settings.rclone_binary if settings else None)
    checks["rclone"] = (
        {"ok": True, "version": info.version, "os": info.os, "arch": info.arch}
        if info
        else {"ok": False, "detail": "binaire rclone introuvable"}
    )

    return {
        "status": "ok" if all(c["ok"] for c in checks.values()) else "degraded",
        "version": __version__,
        "checks": checks,
    }
