"""Autorisation OAuth conduite depuis l'interface (FIRST-002, CLOUD-003, P9).

Le consentement lui-même exige un compte réel et ne peut pas être rejoué
ici. Tout le reste l'est : le lancement de rclone, l'extraction du lien, sa
réécriture vers une adresse joignable, et le refus des retours malformés.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from csm.services.oauth import AUTHORIZE_PORT, OAuthBroker, OAuthError, _rewrite_host

pytestmark = pytest.mark.usefixtures("rclone_path")


# -- réécriture du lien -------------------------------------------------------


def test_the_link_points_at_a_reachable_address() -> None:
    """rclone imprime 127.0.0.1, qui désigne le conteneur — inutilisable
    depuis le navigateur de l'utilisateur."""
    rewritten = _rewrite_host(
        "http://127.0.0.1:53682/auth?state=abc", "192.168.1.205"
    )
    assert rewritten == f"http://192.168.1.205:{AUTHORIZE_PORT}/auth?state=abc"


def test_the_port_of_the_web_interface_is_not_reused() -> None:
    """L'hôte peut arriver avec le port de la WebUI ; seul l'hôte compte."""
    rewritten = _rewrite_host("http://127.0.0.1:53682/auth?state=x", "nas.local:3572")
    assert rewritten.startswith(f"http://nas.local:{AUTHORIZE_PORT}/")


# -- conduite de la session ---------------------------------------------------


def test_starting_yields_a_link_the_browser_can_follow(rclone_path: str) -> None:
    broker = OAuthBroker(rclone_path)
    try:
        started = broker.start("onedrive", "192.168.1.205")
        assert started["session_id"].endswith("-onedrive")
        assert started["auth_url"].startswith(f"http://192.168.1.205:{AUTHORIZE_PORT}/auth")
        assert "state=" in started["auth_url"]
    finally:
        broker.cancel()


def test_an_unknown_provider_is_refused(rclone_path: str) -> None:
    broker = OAuthBroker(rclone_path)
    try:
        with pytest.raises(OAuthError):
            broker.start("fournisseur-inexistant", "192.168.1.205")
    finally:
        broker.cancel()


def test_a_second_authorisation_replaces_the_first(rclone_path: str) -> None:
    """Le port 53682 est unique : deux sessions se marcheraient dessus."""
    broker = OAuthBroker(rclone_path)
    try:
        first = broker.start("dropbox", "192.168.1.205")
        second = broker.start("onedrive", "192.168.1.205")
        assert first["session_id"] != second["session_id"]

        with pytest.raises(OAuthError, match="plus en cours"):
            broker.complete(first["session_id"], "http://localhost/?code=x&state=y")
    finally:
        broker.cancel()


def test_a_redirect_without_a_code_is_refused(rclone_path: str) -> None:
    broker = OAuthBroker(rclone_path)
    try:
        started = broker.start("dropbox", "192.168.1.205")
        with pytest.raises(OAuthError, match="code d'autorisation"):
            broker.complete(started["session_id"], "http://localhost:53682/")
    finally:
        broker.cancel()


def test_an_unknown_session_is_refused(rclone_path: str) -> None:
    broker = OAuthBroker(rclone_path)
    with pytest.raises(OAuthError, match="plus en cours"):
        broker.complete("session-inventee", "http://localhost/?code=x")


# -- API ----------------------------------------------------------------------


def test_the_endpoint_returns_a_usable_link(client: TestClient) -> None:
    response = client.post("/api/oauth/start", json={"provider": "dropbox"})
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["port"] == AUTHORIZE_PORT
    assert "/auth?state=" in payload["auth_url"]
    assert "erreur" in payload["instructions"], "l'écran d'erreur doit être annoncé"

    client.post("/api/oauth/cancel")


def test_the_endpoint_refuses_an_unknown_provider(client: TestClient) -> None:
    response = client.post("/api/oauth/start", json={"provider": "nimportequoi"})
    assert response.status_code == 409


def test_completing_without_a_session_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/oauth/complete",
        json={"session_id": "inexistante", "redirect_url": "http://x/?code=1"},
    )
    assert response.status_code == 409
