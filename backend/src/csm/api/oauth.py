"""Autorisation OAuth conduite depuis l'interface (FIRST-002, CLOUD-003)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from csm.api.deps import get_adapter
from csm.rclone.adapter import RcloneAdapter
from csm.services import oauth
from csm.services.oauth import AUTHORIZE_PORT, OAuthBroker, OAuthError

logger = logging.getLogger("csm.oauth")

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
    try:
        started = broker.start(payload.provider)
    except OAuthError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    return {
        "session_id": started["session_id"],
        # Lien relatif : le navigateur reste sur notre origine, déjà
        # publiée et déjà authentifiée. Le serveur de rclone, lui, n'écoute
        # que sur la boucle locale du conteneur.
        "auth_url": f"/api/oauth/auth?state={started['state']}",
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
            options.update(oauth.describe_drive(oauth.access_token_of(token)))
        except OAuthError as exc:
            logger.warning("disque OneDrive non identifié : %s", exc)
            # Le stockage reste créable — le jeton est bon — mais il sera
            # inutilisable tant que le disque n'est pas renseigné. On le dit
            # franchement plutôt que d'un avertissement qu'on peut manquer.
            return {
                "options": options,
                "error": (
                    f"Jeton obtenu, mais {exc}. Créez tout de même le "
                    "stockage : un bouton « Compléter la configuration » "
                    "permettra de réessayer."
                ),
            }

    return {"options": options}


@router.get("/oauth/auth")
def relay(state: str) -> RedirectResponse:
    """Relaie le navigateur vers la page de consentement du fournisseur.

    rclone ne répond ici qu'une redirection, mais sur un port que seul le
    conteneur peut joindre. On la transmet depuis notre propre port.
    """
    try:
        destination = oauth.provider_redirect(state)
    except OAuthError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return RedirectResponse(destination, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.post("/oauth/cancel", status_code=status.HTTP_204_NO_CONTENT)
def cancel(broker: OAuthBroker = Depends(get_broker)) -> None:
    broker.cancel()
