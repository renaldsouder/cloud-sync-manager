"""Application FastAPI.

Un seul conteneur, un seul port : l'API sert aussi le build du frontend
(§5.1, P4). Les migrations sont appliquées au démarrage pour qu'une mise à
jour d'image ne laisse jamais l'appdata dans un état intermédiaire.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette import status

from csm import __version__
from csm.api import auth as auth_api
from csm.api import config as config_api
from csm.api import oauth as oauth_api
from csm.api import dashboard, health, remotes, tasks
from csm.config import Settings, get_settings
from csm.db import create_db_engine, create_session_factory, sqlite_url
from csm.db.migrate import upgrade_to_head
from csm.rclone.adapter import RcloneAdapter, RcloneUnavailable
from csm.api.auth import PASSWORD_KEY
from csm.services import auth as csm_auth
from csm.services import settings_store
from csm.services.runner import RunManager, mark_orphan_runs_interrupted
from csm.services.scheduler import Scheduler

logger = logging.getLogger("csm")

#: En-têtes appliqués à toutes les réponses (§18). La CSP viendra avec
#: l'authentification de la WebUI (SEC-006), une fois le frontend figé.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "no-referrer",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        response: Response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Exige une session valide sur l'API dès qu'un mot de passe est défini.

    Restent ouverts : les fichiers de l'interface — qui doivent pouvoir
    s'afficher pour proposer l'écran de connexion —, les points
    d'authentification eux-mêmes, et ``/api/health``, cible du HEALTHCHECK
    Docker et qui ne révèle qu'un numéro de version.
    """

    OPEN_PATHS = ("/api/health", "/api/auth/")

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        path = request.url.path
        if not path.startswith("/api") or path.startswith(self.OPEN_PATHS):
            return await call_next(request)

        factory = getattr(request.app.state, "session_factory", None)
        if factory is None:  # pragma: no cover - avant le démarrage complet
            return await call_next(request)

        session = factory()
        try:
            stored = settings_store.get(session, PASSWORD_KEY)
        finally:
            session.close()

        if not stored:
            # Protection non activée : comportement inchangé.
            return await call_next(request)

        token = request.cookies.get(csm_auth.SESSION_COOKIE)
        if csm_auth.read_token(token, request.app.state.auth_secret) is None:
            return JSONResponse(
                {"detail": "authentification requise"},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        return await call_next(request)


def _build_run_manager(app: FastAPI, settings: Settings) -> RunManager | None:
    """Le gestionnaire d'exécutions n'existe que si rclone est présent.

    Son absence ne doit pas empêcher l'application de démarrer : la WebUI
    doit pouvoir afficher un diagnostic plutôt qu'un conteneur mort.
    """
    try:
        adapter = RcloneAdapter.from_settings(settings)
    except RcloneUnavailable:
        logger.warning("rclone introuvable : les exécutions sont désactivées")
        return None
    adapter.config_password = settings.rclone_config_password
    return RunManager(
        app.state.session_factory,
        adapter,
        quarantine_retention_days=settings.quarantine_retention_days,
        quarantine_keep_runs=settings.quarantine_keep_runs,
        filters_dir=settings.config_dir / "filters",
        # Les listings de bisync vivent dans l'appdata : les perdre force une
        # ré-initialisation, donc une fusion des deux côtés.
        bisync_dir=settings.config_dir / "bisync",
        bidirectional_enabled=settings.bidirectional_enabled,
    )


def _build_scheduler(app: FastAPI, settings: Settings) -> Scheduler | None:
    """Planificateur interne, actif dès qu'une exécution est possible."""
    if app.state.run_manager is None:
        return None

    scheduler = Scheduler(
        app.state.session_factory,
        app.state.run_manager,
        poll_seconds=settings.scheduler_poll_seconds,
        history_retention_days=settings.history_retention_days,
        history_keep_runs=settings.history_keep_runs,
    )
    # La politique de rattrapage s'applique avant tout battement : le §11
    # interdit de rejouer en rafale les occurrences manquées.
    scheduler.prime()
    scheduler.purge_history()
    if settings.scheduler_enabled:
        scheduler.start()
    return scheduler


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.ensure_dirs()
        upgrade_to_head(sqlite_url(settings.db_path))
        engine = create_db_engine(settings.db_path)
        app.state.settings = settings
        app.state.engine = engine
        # Clé de signature des sessions : créée au premier démarrage,
        # sa suppression révoque toutes les sessions ouvertes.
        app.state.auth_secret = csm_auth.load_or_create_secret(
            settings.config_dir / "session.key"
        )
        app.state.login_throttle = csm_auth.LoginThrottle()
        app.state.session_factory = create_session_factory(engine)
        mark_orphan_runs_interrupted(app.state.session_factory)
        app.state.run_manager = _build_run_manager(app, settings)
        app.state.scheduler = _build_scheduler(app, settings)
        logger.info("Cloud Sync Manager %s prêt sur %s", __version__, settings.config_dir)
        try:
            yield
        finally:
            if app.state.scheduler is not None:
                app.state.scheduler.stop()
            if app.state.run_manager is not None:
                # Une exécution laissée derrière serait comptée « Réussie »
                # au prochain démarrage alors qu'elle a été coupée (§8.5).
                app.state.run_manager.stop_all()
            engine.dispose()

    app = FastAPI(
        title="Cloud Sync Manager",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AuthenticationMiddleware)
    app.include_router(auth_api.router, prefix="/api")
    app.include_router(health.router, prefix="/api")
    app.include_router(remotes.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    app.include_router(dashboard.router, prefix="/api")
    app.include_router(config_api.router, prefix="/api")
    app.include_router(oauth_api.router, prefix="/api")

    if settings.web_dir.is_dir():
        # html=True sert index.html à la racine. Le repli SPA sur les routes
        # profondes sera ajouté avec le routeur frontend (UI-001).
        app.mount("/", StaticFiles(directory=settings.web_dir, html=True), name="web")
    else:

        @app.get("/")
        def _no_frontend() -> JSONResponse:
            return JSONResponse(
                {
                    "detail": "Frontend non construit.",
                    "hint": "npm --prefix frontend run build, ou utilisez l'image Docker.",
                    "api": "/api/docs",
                },
                status_code=503,
            )

    return app


app = create_app()
