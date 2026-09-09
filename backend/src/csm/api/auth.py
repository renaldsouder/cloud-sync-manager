"""Connexion à l'interface Web (SEC-006).

La protection est **facultative** : le §18 la demande « lorsqu'elle est
exposée hors LAN », et rien ne permet de détecter cette exposition depuis le
conteneur. L'imposer verrouillerait aussi les installations existantes lors
d'une mise à jour, ce que le §17 déconseille.

Elle n'est donc pas activée par défaut — mais tant qu'elle ne l'est pas,
l'application le dit, sur chaque appel qui décrit son état.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from csm.api.deps import get_session
from csm.services import auth, settings_store

router = APIRouter(tags=["authentification"])

PASSWORD_KEY = "auth.password_hash"


class LoginIn(BaseModel):
    password: str = Field(min_length=1)
    #: Session courte par défaut ; l'utilisateur choisit d'être reconnu.
    remember: bool = False


class PasswordIn(BaseModel):
    #: Requis dès qu'un mot de passe existe déjà : sans lui, quiconque a une
    #: session ouverte pourrait en changer sans connaître l'actuel.
    current_password: str | None = None
    new_password: str | None = Field(default=None, min_length=0)


def _secret(request: Request) -> bytes:
    return request.app.state.auth_secret


def _throttle(request: Request) -> auth.LoginThrottle:
    return request.app.state.login_throttle


def is_enabled(session: Session) -> bool:
    return bool(settings_store.get(session, PASSWORD_KEY))


@router.get("/auth/session")
def read_session(
    request: Request, session: Session = Depends(get_session)
) -> dict[str, Any]:
    """État de la protection et de la session courante."""
    enabled = is_enabled(session)
    authenticated = True
    if enabled:
        token = request.cookies.get(auth.SESSION_COOKIE)
        authenticated = auth.read_token(token, _secret(request)) is not None
    return {"enabled": enabled, "authenticated": authenticated}


@router.post("/auth/login")
def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    stored = settings_store.get(session, PASSWORD_KEY)
    if not stored:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "aucun mot de passe n'est défini : l'interface n'est pas protégée",
        )

    origin = request.client.host if request.client else "inconnu"
    throttle = _throttle(request)
    if (wait := throttle.blocked_for(origin)) > 0:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"trop de tentatives — réessayez dans {int(wait)} secondes",
        )

    if not auth.verify_password(payload.password, stored):
        throttle.record_failure(origin)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "mot de passe incorrect")

    throttle.clear(origin)
    lifetime = auth.DEFAULT_LIFETIME if payload.remember else 12 * 3600
    _set_cookie(response, auth.issue_token(_secret(request), lifetime), lifetime)
    return {"authenticated": True}


@router.post("/auth/logout")
def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"authenticated": False}


@router.put("/auth/password")
def set_password(
    payload: PasswordIn,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Définit, change ou retire le mot de passe.

    Un ``new_password`` vide retire la protection — opération volontairement
    explicite, et refusée sans le mot de passe actuel.
    """
    stored = settings_store.get(session, PASSWORD_KEY)

    if stored and not auth.verify_password(payload.current_password or "", stored):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "le mot de passe actuel est incorrect"
        )

    if not payload.new_password:
        settings_store.put(session, PASSWORD_KEY, None)
        session.flush()
        response.delete_cookie(auth.SESSION_COOKIE, path="/")
        return {"enabled": False}

    try:
        settings_store.put(session, PASSWORD_KEY, auth.hash_password(payload.new_password))
    except auth.AuthError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    session.flush()
    # On ouvre la session immédiatement : demander de se reconnecter juste
    # après avoir choisi son mot de passe n'apporte rien.
    _set_cookie(
        response, auth.issue_token(_secret(request), auth.DEFAULT_LIFETIME), auth.DEFAULT_LIFETIME
    )
    return {"enabled": True}


def _set_cookie(response: Response, token: str, lifetime: int) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=lifetime,
        httponly=True,
        samesite="lax",
        path="/",
        # `secure` n'est pas posé : l'application est servie en HTTP derrière
        # le reverse proxy qui, lui, porte le TLS. L'exiger ici empêcherait
        # toute connexion sur un réseau local en clair.
        secure=False,
    )
