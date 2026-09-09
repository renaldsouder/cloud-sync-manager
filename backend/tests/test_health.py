from __future__ import annotations

from fastapi.testclient import TestClient

from csm import __version__


def test_health_reports_database_and_version(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200

    payload = response.json()
    assert payload["version"] == __version__
    assert payload["checks"]["database"]["ok"] is True
    # rclone peut être absent de la machine de développement : le point de
    # santé doit le dire sans faire tomber l'API (« degraded », pas 500).
    assert payload["status"] in {"ok", "degraded"}
    assert "rclone" in payload["checks"]


def test_security_headers_are_applied(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_appdata_layout_is_created(client: TestClient) -> None:
    settings = client.app.state.settings
    assert settings.db_path.exists()
    assert settings.log_dir.is_dir()


def test_root_explains_missing_frontend(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 503
    assert response.json()["api"] == "/api/docs"
