"""Ré-autorisation OAuth d'un stockage existant.

Un jeton expiré ou révoqué obligeait à recréer le stockage, puis chacune
des tâches qui s'en servent. Ces tests vérifient qu'on remplace le seul
jeton, et surtout qu'une reconnexion avec un autre compte ne peut pas
rediriger en silence une tâche destructive vers d'autres données.
"""

from __future__ import annotations

import configparser
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.config import Settings
from csm.db.models import Task
from tests.test_tasks_api import make_cloud, make_task

pytestmark = pytest.mark.usefixtures("rclone_path")

OLD_TOKEN = json.dumps({"access_token": "ancien-jeton", "expiry": "2026-01-01T00:00:00Z"})
NEW_TOKEN = json.dumps({"access_token": "nouveau-jeton", "expiry": "2026-12-01T00:00:00Z"})


def _onedrive(client: TestClient, drive_id: str = "disque-A") -> str:
    response = client.post(
        "/api/remotes",
        json={
            "name": "OneDrive",
            "provider": "onedrive",
            "options": {"token": OLD_TOKEN, "drive_id": drive_id, "drive_type": "personal"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _section(settings: Settings, client: TestClient, remote_id: str) -> dict[str, str]:
    remote = next(r for r in client.get("/api/remotes").json() if r["id"] == remote_id)
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(settings.rclone_config_path, encoding="utf-8")
    return dict(parser[remote["rclone_remote_name"]])


def _reauthorize(client: TestClient, remote_id: str, **body: str):
    return client.post(f"/api/remotes/{remote_id}/reauthorize", json=body)


def test_only_the_token_is_replaced(client: TestClient, settings: Settings) -> None:
    remote_id = _onedrive(client)
    response = _reauthorize(client, remote_id, token=NEW_TOKEN, drive_id="disque-A")
    assert response.status_code == 200, response.text

    section = _section(settings, client, remote_id)
    assert json.loads(section["token"])["access_token"] == "nouveau-jeton"
    assert section["drive_id"] == "disque-A"
    assert section["drive_type"] == "personal"
    # Le secret ne ressort pas par l'API de lecture (SEC-002).
    listed = next(r for r in client.get("/api/remotes").json() if r["id"] == remote_id)
    assert "nouveau-jeton" not in json.dumps(listed)


def test_another_onedrive_account_is_refused(
    client: TestClient, settings: Settings
) -> None:
    """Le cas dangereux : les tâches pointeraient vers le disque d'un autre
    compte. Refus, et l'ancien jeton reste en place."""
    remote_id = _onedrive(client)
    response = _reauthorize(client, remote_id, token=NEW_TOKEN, drive_id="disque-B")
    assert response.status_code == 409
    assert "autre disque" in response.json()["detail"]
    section = _section(settings, client, remote_id)
    assert json.loads(section["token"])["access_token"] == "ancien-jeton"


def test_destructive_tasks_must_simulate_again(
    client: TestClient, local_root: Path
) -> None:
    """Pour les fournisseurs où l'on ne peut pas reconnaître le compte, la
    simulation réarmée est le filet : un Miroir ne repart pas sans montrer
    ce qu'il ferait."""
    remote_id = _onedrive(client)
    for name, mode in (("Miroir", "mirror"), ("Copie", "copy")):
        folder = local_root / name
        folder.mkdir()
        make_task(client, remote_id, folder, name=name, mode=mode)

    session = client.app.state.session_factory()
    try:
        for task in session.query(Task).all():
            task.dry_run_required = False
        session.commit()
    finally:
        session.close()

    response = _reauthorize(client, remote_id, token=NEW_TOKEN)
    assert response.status_code == 200, response.text
    assert response.json()["simulation_required"] == ["Miroir"]

    tasks = {t["name"]: t for t in client.get("/api/tasks").json()}
    assert tasks["Miroir"]["dry_run_required"] is True
    assert tasks["Copie"]["dry_run_required"] is False


@pytest.mark.parametrize(
    "token", ["pas du json", json.dumps({"refresh_token": "x"}), json.dumps(["x"])]
)
def test_an_unreadable_token_is_refused(
    client: TestClient, settings: Settings, token: str
) -> None:
    remote_id = _onedrive(client)
    assert _reauthorize(client, remote_id, token=token).status_code == 409
    section = _section(settings, client, remote_id)
    assert json.loads(section["token"])["access_token"] == "ancien-jeton"


def test_a_remote_without_oauth_is_refused(client: TestClient, tmp_path: Path) -> None:
    remote_id = make_cloud(client, tmp_path / "cloud")
    response = _reauthorize(client, remote_id, token=NEW_TOKEN)
    assert response.status_code == 409
    assert "OAuth" in response.json()["detail"]


def test_unknown_fields_are_refused(client: TestClient) -> None:
    """Le geste ne remplace que le jeton : une autre option glissée dans la
    requête n'a rien à y faire."""
    remote_id = _onedrive(client)
    response = _reauthorize(client, remote_id, token=NEW_TOKEN, root_folder_id="x")
    assert response.status_code == 422


def test_refused_while_a_task_on_the_remote_runs(
    client: TestClient, local_root: Path
) -> None:
    """rclone réécrit le jeton rafraîchi en fin d'exécution : il écraserait
    celui qu'on vient d'enregistrer."""
    remote_id = _onedrive(client)
    folder = local_root / "Photos"
    folder.mkdir()
    task = make_task(client, remote_id, folder)

    runner = client.app.state.run_manager
    original = runner.is_running
    runner.is_running = lambda task_id: task_id == task["id"]  # type: ignore[method-assign]
    try:
        response = _reauthorize(client, remote_id, token=NEW_TOKEN)
    finally:
        runner.is_running = original  # type: ignore[method-assign]
    assert response.status_code == 409
    assert "en ce moment" in response.json()["detail"]
