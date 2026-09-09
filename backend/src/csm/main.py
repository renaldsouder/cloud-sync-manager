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

from csm import __version__
from csm.api import health
from csm.config import Settings, get_settings
from csm.db import create_db_engine, create_session_factory, sqlite_url
from csm.db.migrate import upgrade_to_head

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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.ensure_dirs()
        upgrade_to_head(sqlite_url(settings.db_path))
        engine = create_db_engine(settings.db_path)
        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        logger.info("Cloud Sync Manager %s prêt sur %s", __version__, settings.config_dir)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="Cloud Sync Manager",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(health.router, prefix="/api")

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
