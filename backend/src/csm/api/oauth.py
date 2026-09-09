"""Autorisation OAuth conduite depuis l'interface (FIRST-002, CLOUD-003)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from csm.api.deps import get_adapter
from csm.rclone.adapter import RcloneAdapter
from csm.services import oauth
from csm.services.oauth import AUTHORIZE_PORT, OAuthBroker, OAuthError

router = APIRouter(tags=["autorisation"])


class StartIn(BaseModel):
    provider: str = Field(min_length=1, max_length=64)


class CompleteIn(BaseModel):
    session_id: str
    #: L'adresse complète de la page d'erreur sur laquelle le navigateur
    #: atterrit après le consentement — c'est elle qui porte le code.
    redirect_url: str = Field(min_length=1)


def get_broker(request: Request, adapter: RcloneAdapter = Depends(get_adapter)) -> OAuthBroker:
    broker = getattr(request.app.state, "oauth_broker", None)
    if broker is None:
        broker = OAuthBroker(adapter.binary)
        request.app.state.oauth_broker = broker
    return broker


@router.post("/oauth/start")
def start(
    payload: StartIn, request: Request, broker: OAuthBroker = Depends(get_broker)
) -> dict[str, Any]:
    """Ouvre une autorisation et rend le lien à suivre dans le navigateur."""
    # L'adresse par laquelle l'utilisateur nous joint est la seule que son
    # navigateur saura atteindre : le lien de rclone désigne le conteneur.
    host = request.headers.get("host", "").split(":")[0] or "127.0.0.1"
    try:
        started = broker.start(payload.provider, host)
    except OAuthError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    return {
        **started,
        "port": AUTHORIZE_PORT,
        "instructions": (
            "Ouvrez ce lien, autorisez l'accès, puis recopiez ici l'adresse "
            "complète de la page sur laquelle votre navigateur atterrit — "
            "elle affichera une erreur de connexion, c'est normal."
        ),
    }


@router.post("/oauth/complete")
def complete(
    payload: CompleteIn,
    broker: OAuthBroker = Depends(get_broker),
) -> dict[str, Any]:
    """Achève l'autorisation et rend les champs à enregistrer."""
    try:
        token = broker.complete(payload.session_id, payload.redirect_url)
    except OAuthError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    options: dict[str, str] = {"token": token}

    # OneDrive exige deux champs de plus, que le dialogue interactif de
    # rclone découvre en interrogeant Microsoft. On fait de même, plutôt que
    # de renvoyer l'utilisateur au terminal pour les chercher.
    if payload.session_id.endswith("-onedrive"):
        try:
            access_token = json.loads(token).get("access_token", "")
            options.update(oauth.describe_drive(access_token))
        except (ValueError, OAuthError) as exc:
            return {
                "options": options,
                "warning": (
                    "Jeton obtenu, mais le disque OneDrive n'a pas pu être "
                    f"identifié ({exc}). Renseignez drive_id et drive_type "
                    "à la main si le test échoue."
                ),
            }

    return {"options": options}


@router.post("/oauth/cancel", status_code=status.HTTP_204_NO_CONTENT)
def cancel(broker: OAuthBroker = Depends(get_broker)) -> None:
    broker.cancel()
