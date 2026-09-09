"""API des stockages Cloud (CLOUD-001 → CLOUD-006, SEC-001)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("rclone_path")

SECRET = "MotDePasseSFTP!42"


def _create_alias(client: TestClient, target: Path, name: str = "Dossier local") -> dict:
    response = client.post(
        "/api/remotes",
        json={"name": name, "provider": "alias", "options": {"remote": str(target)}},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_priority_providers_are_listed_in_order(client: TestClient) -> None:
    payload = client.get("/api/providers").json()
    names = [item["name"] for item in payload]
    assert names == ["drive", "onedrive", "dropbox", "s3", "b2", "webdav", "sftp"]
    assert all(item["priority"] for item in payload)
    # Les trois premiers exigent un aller-retour navigateur (P9) : l'UI doit
    # pouvoir le signaler avant que l'utilisateur ne commence.
    assert [item["needs_oauth"] for item in payload[:3]] == [True, True, True]


def test_advanced_mode_exposes_the_whole_catalogue(client: TestClient) -> None:
    payload = client.get("/api/providers", params={"include_all": True}).json()
    assert len(payload) > 20
    assert any(item["name"] == "alias" for item in payload)


def test_create_list_and_delete(client: TestClient, tmp_path: Path) -> None:
    target = tmp_path / "données"
    (target / "Photos").mkdir(parents=True)

    created = _create_alias(client, target)
    assert created["provider"] == "alias"
    assert created["status"] == "unknown"
    assert created["rclone_remote_name"] == "Dossier-local"

    listed = client.get("/api/remotes").json()
    assert [item["id"] for item in listed] == [created["id"]]
    assert listed[0]["task_count"] == 0

    assert client.delete(f"/api/remotes/{created['id']}").status_code == 204
    assert client.get("/api/remotes").json() == []


def test_duplicate_name_is_refused(client: TestClient, tmp_path: Path) -> None:
    target = tmp_path / "d"
    target.mkdir()
    _create_alias(client, target)
    response = client.post(
        "/api/remotes",
        json={"name": "Dossier local", "provider": "alias", "options": {"remote": str(target)}},
    )
    assert response.status_code == 409
    assert "existe déjà" in response.json()["detail"]


def test_test_endpoint_reports_capabilities(client: TestClient, tmp_path: Path) -> None:
    target = tmp_path / "données"
    (target / "Photos").mkdir(parents=True)
    (target / "Documents").mkdir()

    created = _create_alias(client, target)
    result = client.post(f"/api/remotes/{created['id']}/test").json()

    assert result["ok"] is True
    assert result["capabilities"]["list"] is True
    assert set(result["entries"]) == {"Photos", "Documents"}
    assert result["elapsed_ms"] >= 0

    refreshed = client.get("/api/remotes").json()[0]
    assert refreshed["status"] == "ok"
    assert refreshed["last_test_at"] is not None


def test_test_endpoint_reports_failure_with_technical_cause(
    client: TestClient, tmp_path: Path
) -> None:
    """§8.2 / §27.10 — une source inaccessible échoue, avec sa cause."""
    created = _create_alias(client, tmp_path / "nexiste-pas")
    result = client.post(f"/api/remotes/{created['id']}/test").json()

    assert result["ok"] is False
    assert result["detail"]
    assert client.get("/api/remotes").json()[0]["status"] == "error"


def test_password_is_obscured_on_disk_and_never_returned(
    client: TestClient, tmp_path: Path
) -> None:
    """SEC-001 — le mot de passe ne doit exister nulle part en clair."""
    response = client.post(
        "/api/remotes",
        json={
            "name": "NAS distant",
            "provider": "sftp",
            "options": {"host": "192.168.1.10", "user": "renald", "pass": SECRET},
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()

    settings = client.app.state.settings
    raw = settings.rclone_config_path.read_text(encoding="utf-8")
    assert SECRET not in raw, "mot de passe écrit en clair dans rclone.conf"
    assert "pass = " in raw

    # L'API ne renvoie jamais la valeur, même obscurcie (§6.1).
    assert created["options"]["pass"] == "configuré"
    assert created["options"]["host"] == "192.168.1.10"
    assert SECRET not in response.text


def test_updating_keeps_a_secret_left_untouched(
    client: TestClient, tmp_path: Path
) -> None:
    """Rééditer un stockage sans retaper le mot de passe ne doit pas l'effacer."""
    created = client.post(
        "/api/remotes",
        json={
            "name": "NAS distant",
            "provider": "sftp",
            "options": {"host": "192.168.1.10", "user": "renald", "pass": SECRET},
        },
    ).json()

    settings = client.app.state.settings
    before = settings.rclone_config_path.read_text(encoding="utf-8")

    updated = client.patch(
        f"/api/remotes/{created['id']}",
        json={
            "name": "NAS du bureau",
            "options": {"host": "192.168.1.20", "user": "renald", "pass": "configuré"},
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "NAS du bureau"
    assert updated.json()["options"]["host"] == "192.168.1.20"
    assert updated.json()["options"]["pass"] == "configuré"

    after = settings.rclone_config_path.read_text(encoding="utf-8")
    obscured_before = _extract(before, "pass")
    assert _extract(after, "pass") == obscured_before
    assert SECRET not in after


def test_unknown_remote_returns_404(client: TestClient) -> None:
    assert client.post("/api/remotes/inexistant/test").status_code == 404
    assert client.delete("/api/remotes/inexistant").status_code == 404


def _extract(config_text: str, key: str) -> str:
    for line in config_text.splitlines():
        if line.startswith(f"{key} = "):
            return line.split(" = ", 1)[1]
    raise AssertionError(f"clé {key} absente de la configuration")
