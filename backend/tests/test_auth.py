"""Protection de l'interface Web (SEC-006, §18)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.services.auth import (
    AuthError,
    LoginThrottle,
    hash_password,
    issue_token,
    load_or_create_secret,
    read_token,
    verify_password,
)

PASSWORD = "un-mot-de-passe-solide"


# -- empreinte du mot de passe ------------------------------------------------


def test_hashing_is_salted_and_verifiable() -> None:
    first = hash_password(PASSWORD)
    second = hash_password(PASSWORD)

    assert first != second, "deux empreintes du même mot de passe doivent différer"
    assert PASSWORD not in first
    assert verify_password(PASSWORD, first)
    assert verify_password(PASSWORD, second)
    assert not verify_password("autre chose", first)


def test_a_short_password_is_refused() -> None:
    with pytest.raises(AuthError):
        hash_password("court")


@pytest.mark.parametrize(
    "stored", [None, "", "pas-une-empreinte", "scrypt$abc", "bcrypt$1$1$1$aa$bb"]
)
def test_a_malformed_hash_never_authenticates(stored: str | None) -> None:
    assert verify_password(PASSWORD, stored) is False


# -- jeton de session ---------------------------------------------------------


def test_a_token_survives_a_restart(tmp_path: Path) -> None:
    """Le jeton est signé, pas stocké : redémarrer ne déconnecte personne."""
    key = tmp_path / "session.key"
    secret = load_or_create_secret(key)
    token = issue_token(secret)

    reloaded = load_or_create_secret(key)
    assert reloaded == secret
    assert read_token(token, reloaded) is not None


def test_a_key_bordered_by_whitespace_bytes_is_kept_intact(tmp_path: Path) -> None:
    """Une clé est faite d'octets aléatoires, pas de texte.

    Environ une sur vingt commence ou finit par un octet d'espacement. La
    rogner à la relecture invaliderait toutes les sessions au redémarrage —
    défaut resté invisible tant que le tirage était favorable.
    """
    key = tmp_path / "session.key"
    piegee = bytes([10, 9, 32]) + bytes([1]) * 40 + bytes([32, 13, 10])
    key.write_bytes(piegee)

    relue = load_or_create_secret(key)

    assert relue == piegee, "la clé relue doit être identique, octet pour octet"
    assert read_token(issue_token(relue), load_or_create_secret(key)) is not None


def test_the_key_returned_at_creation_matches_the_one_on_disk(tmp_path: Path) -> None:
    key = tmp_path / "session.key"
    creee = load_or_create_secret(key)
    assert creee == key.read_bytes()


def test_deleting_the_key_revokes_every_session(tmp_path: Path) -> None:
    """C'est le moyen de reprendre la main si un poste a été compromis."""
    key = tmp_path / "session.key"
    token = issue_token(load_or_create_secret(key))
    key.unlink()

    assert read_token(token, load_or_create_secret(key)) is None


def test_a_forged_or_expired_token_is_refused(tmp_path: Path) -> None:
    secret = load_or_create_secret(tmp_path / "session.key")

    assert read_token(None, secret) is None
    assert read_token("nimporte-quoi", secret) is None
    assert read_token("9999999999.signature-inventee", secret) is None
    # Expiration réécrite pour la prolonger : la signature ne suit pas.
    token = issue_token(secret, lifetime=60)
    tampered = f"{int(time.time()) + 99999}.{token.split('.', 1)[1]}"
    assert read_token(tampered, secret) is None
    # Jeton correctement signé mais périmé.
    assert read_token(issue_token(secret, lifetime=-10), secret) is None


def test_repeated_attempts_are_slowed_down() -> None:
    throttle = LoginThrottle(allowed=3, window=300.0)

    for _ in range(3):
        assert throttle.blocked_for("10.0.0.1") == 0
        throttle.record_failure("10.0.0.1")

    assert throttle.blocked_for("10.0.0.1") > 0
    assert throttle.blocked_for("10.0.0.2") == 0, "une autre origine n'est pas punie"

    throttle.clear("10.0.0.1")
    assert throttle.blocked_for("10.0.0.1") == 0


# -- API ----------------------------------------------------------------------


def test_without_a_password_everything_stays_open(client: TestClient) -> None:
    """Le comportement d'une installation existante ne doit pas changer."""
    state = client.get("/api/auth/session").json()
    assert state == {"enabled": False, "authenticated": True}
    assert client.get("/api/tasks").status_code == 200


def test_setting_a_password_protects_the_api(client: TestClient) -> None:
    created = client.put("/api/auth/password", json={"new_password": PASSWORD})
    assert created.status_code == 200
    assert created.json()["enabled"] is True

    # La session est ouverte immédiatement après la création.
    assert client.get("/api/tasks").status_code == 200

    client.cookies.clear()
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/remotes").status_code == 401
    assert client.get("/api/config/export").status_code == 401


def test_health_and_login_stay_reachable_when_locked(client: TestClient) -> None:
    """Le HEALTHCHECK doit continuer de répondre, sinon Unraid croit l'app morte."""
    client.put("/api/auth/password", json={"new_password": PASSWORD})
    client.cookies.clear()

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/auth/session").json() == {
        "enabled": True,
        "authenticated": False,
    }


def test_login_and_logout(client: TestClient) -> None:
    client.put("/api/auth/password", json={"new_password": PASSWORD})
    client.cookies.clear()

    refused = client.post("/api/auth/login", json={"password": "mauvais-mot-de-passe"})
    assert refused.status_code == 401

    accepted = client.post("/api/auth/login", json={"password": PASSWORD})
    assert accepted.status_code == 200
    assert client.get("/api/tasks").status_code == 200

    client.post("/api/auth/logout")
    assert client.get("/api/tasks").status_code == 401


def test_the_cookie_is_not_readable_by_scripts(client: TestClient) -> None:
    client.put("/api/auth/password", json={"new_password": PASSWORD})
    client.cookies.clear()
    response = client.post("/api/auth/login", json={"password": PASSWORD})

    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_changing_the_password_requires_the_current_one(client: TestClient) -> None:
    client.put("/api/auth/password", json={"new_password": PASSWORD})

    refused = client.put(
        "/api/auth/password",
        json={"current_password": "faux", "new_password": "un-autre-mot-de-passe"},
    )
    assert refused.status_code == 403

    accepted = client.put(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": "un-autre-mot-de-passe"},
    )
    assert accepted.status_code == 200
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 401


def test_removing_the_protection_is_explicit(client: TestClient) -> None:
    client.put("/api/auth/password", json={"new_password": PASSWORD})

    refused = client.put("/api/auth/password", json={"new_password": ""})
    assert refused.status_code == 403, "retirer la protection sans mot de passe actuel"

    removed = client.put(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": ""}
    )
    assert removed.status_code == 200
    assert removed.json()["enabled"] is False
    assert client.get("/api/tasks").status_code == 200


def test_brute_force_is_throttled(client: TestClient) -> None:
    client.put("/api/auth/password", json={"new_password": PASSWORD})
    client.cookies.clear()

    codes = [
        client.post("/api/auth/login", json={"password": f"essai-{i}"}).status_code
        for i in range(7)
    ]
    assert 429 in codes, "les tentatives répétées doivent finir par être bloquées"

    # Même le bon mot de passe est refusé tant que le blocage dure.
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 429
