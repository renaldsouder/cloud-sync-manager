"""Autorisation OAuth conduite depuis l'interface (FIRST-002, CLOUD-003, P9).

Le consentement lui-même exige un compte réel et ne peut pas être rejoué
ici. Tout le reste l'est : le lancement de rclone, l'extraction du jeton
anti-rejeu, le relais vers la page du fournisseur, et le refus des retours
malformés.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from csm.services.oauth import AUTHORIZE_PORT, OAuthBroker, OAuthError, _state_of

pytestmark = pytest.mark.usefixtures("rclone_path")


# -- lecture du lien de rclone ------------------------------------------------


def test_the_state_is_extracted() -> None:
    assert _state_of("http://127.0.0.1:53682/auth?state=abc123") == "abc123"
    assert _state_of("http://127.0.0.1:53682/auth") == ""


# -- conduite de la session ---------------------------------------------------


def test_starting_yields_a_state(rclone_path: str) -> None:
    broker = OAuthBroker(rclone_path)
    try:
        started = broker.start("onedrive")
        assert started["session_id"].endswith("-onedrive")
        assert started["state"], "rclone doit fournir un jeton anti-rejeu"
    finally:
        broker.cancel()


def test_an_unknown_provider_is_refused(rclone_path: str) -> None:
    broker = OAuthBroker(rclone_path)
    try:
        with pytest.raises(OAuthError):
            broker.start("fournisseur-inexistant")
    finally:
        broker.cancel()


def test_a_second_authorisation_replaces_the_first(rclone_path: str) -> None:
    """Le port 53682 est unique : deux sessions se marcheraient dessus."""
    broker = OAuthBroker(rclone_path)
    try:
        first = broker.start("dropbox")
        second = broker.start("onedrive")
        assert first["session_id"] != second["session_id"]

        with pytest.raises(OAuthError, match="plus en cours"):
            broker.complete(first["session_id"], "http://localhost/?code=x&state=y")
    finally:
        broker.cancel()


def test_a_redirect_without_a_code_is_refused(rclone_path: str) -> None:
    broker = OAuthBroker(rclone_path)
    try:
        started = broker.start("dropbox")
        with pytest.raises(OAuthError, match="code d'autorisation"):
            broker.complete(started["session_id"], "http://localhost:53682/")
    finally:
        broker.cancel()


def test_an_unknown_session_is_refused(rclone_path: str) -> None:
    broker = OAuthBroker(rclone_path)
    with pytest.raises(OAuthError, match="plus en cours"):
        broker.complete("session-inventee", "http://localhost/?code=x")


# -- API ----------------------------------------------------------------------


def test_the_link_stays_on_our_own_origin(client: TestClient) -> None:
    """rclone n'écoute que sur 127.0.0.1, la boucle locale du conteneur.

    Publier son port ne sert à rien : le trafic arriverait sur l'interface
    réseau, où personne n'écoute. Le lien doit donc désigner notre
    application, qui relaiera.
    """
    response = client.post("/api/oauth/start", json={"provider": "dropbox"})
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["auth_url"].startswith("/api/oauth/auth?state=")
    assert str(AUTHORIZE_PORT) not in payload["auth_url"]
    assert "erreur" in payload["instructions"], "l'écran d'erreur doit être annoncé"

    client.post("/api/oauth/cancel")


def test_the_relay_forwards_to_the_provider(client: TestClient) -> None:
    """Du clic jusqu'à la page de consentement — le maillon qui manquait."""
    started = client.post("/api/oauth/start", json={"provider": "dropbox"}).json()
    try:
        relayed = client.get(started["auth_url"], follow_redirects=False)
        assert relayed.status_code == 307

        location = relayed.headers["location"]
        assert location.startswith("https://"), "le navigateur part chez le fournisseur"
        assert "dropbox.com" in location
        assert "redirect_uri=" in location
    finally:
        client.post("/api/oauth/cancel")


def test_the_relay_refuses_an_unknown_state(client: TestClient) -> None:
    client.post("/api/oauth/cancel")
    assert client.get("/api/oauth/auth?state=inconnu").status_code == 409


def test_the_endpoint_refuses_an_unknown_provider(client: TestClient) -> None:
    assert (
        client.post("/api/oauth/start", json={"provider": "nimportequoi"}).status_code
        == 409
    )


def test_completing_without_a_session_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/oauth/complete",
        json={"session_id": "inexistante", "redirect_url": "http://x/?code=1"},
    )
    assert response.status_code == 409


# -- identification du disque OneDrive ----------------------------------------


def test_the_access_token_is_extracted_from_the_blob() -> None:
    from csm.services.oauth import access_token_of

    assert access_token_of('{"access_token": "abc", "expiry": "2026"}') == "abc"


@pytest.mark.parametrize(
    "blob", ["", "pas du json", "{}", '{"refresh_token": "seulement"}', "[1, 2]"]
)
def test_a_blob_without_token_is_refused(blob: str) -> None:
    from csm.services.oauth import access_token_of

    with pytest.raises(OAuthError):
        access_token_of(blob)


def test_a_refused_token_names_the_cause() -> None:
    """§27.10 — « disque introuvable » n'aide personne ; le motif si."""
    from csm.services.oauth import describe_drive

    with pytest.raises(OAuthError) as excinfo:
        describe_drive("jeton-invalide")

    message = str(excinfo.value)
    assert "401" in message or "jeton" in message.lower()
    # Les deux points d'entrée sont tentés avant d'abandonner.
    assert "drive" in message and "drives" in message


def test_completing_a_remote_that_is_not_onedrive_is_refused(
    client: TestClient, tmp_path
) -> None:
    from tests.test_tasks_api import make_cloud

    remote_id = make_cloud(client, tmp_path / "cloud")
    refused = client.post(f"/api/remotes/{remote_id}/complete")
    assert refused.status_code == 409
    assert "OneDrive" in refused.json()["detail"]


def test_completing_a_onedrive_without_token_is_refused(client: TestClient) -> None:
    """Le message doit dire quoi faire, pas seulement que ça a échoué."""
    created = client.post(
        "/api/remotes",
        json={"name": "OD sans jeton", "provider": "onedrive", "options": {}},
    )
    assert created.status_code == 201, created.text

    refused = client.post(f"/api/remotes/{created.json()['id']}/complete")
    assert refused.status_code == 409
    assert "relancez l'autorisation" in refused.json()["detail"]
