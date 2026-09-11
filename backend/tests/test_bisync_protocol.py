"""Protocole d'initialisation du bidirectionnel (SYNC-003, §7.3, §8.1).

bisync ne compare rien tant qu'une ré-initialisation n'a pas établi la
référence commune. Cette ré-initialisation **fusionne les deux côtés** : elle
ne doit jamais partir d'elle-même, et elle doit avoir été simulée avant d'être
appliquée.

Le mode reste fermé par défaut tant que la matrice du §20.3 n'est pas
couverte ; ``test_the_mode_is_closed_by_default`` verrouille cette promesse.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from csm.config import Settings
from csm.db.models import Task
from csm.main import create_app
from csm.services.runner import bisync_settings

pytestmark = pytest.mark.usefixtures("rclone_path")


@pytest.fixture
def ouvert(tmp_path: Path, local_root: Path, rclone_path: str) -> Iterator[TestClient]:
    """Une instance où le bidirectionnel est déverrouillé."""
    settings = Settings(
        config_dir=tmp_path / "config",
        web_dir=tmp_path / "no-web",
        allowed_roots=str(local_root),
        rclone_binary=rclone_path,
        scheduler_enabled=False,
        bidirectional_enabled=True,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def make_bisync_task(
    client: TestClient, local: Path, cloud: Path, name: str = "Deux sens"
) -> str:
    local.mkdir(parents=True, exist_ok=True)
    cloud.mkdir(parents=True, exist_ok=True)
    remote = client.post(
        "/api/remotes",
        json={"name": f"Cloud {name}", "provider": "alias", "options": {"remote": str(cloud)}},
    )
    assert remote.status_code == 201, remote.text
    task = client.post(
        "/api/tasks",
        json={
            "name": name,
            "remote_id": remote.json()["id"],
            "local_path": str(local),
            "remote_path": "",
            "direction": "local_to_remote",
            "mode": "bisync",
        },
    )
    assert task.status_code == 201, task.text
    return task.json()["id"]


def run(client: TestClient, task_id: str, **payload) -> dict:
    body = {"dry_run": True, **payload}
    return client.post(f"/api/tasks/{task_id}/run", json=body)


def wait(client: TestClient, run_id: str, timeout: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = client.get(f"/api/runs/{run_id}").json()
        if current["status"] != "running":
            return current
        time.sleep(0.1)
    raise AssertionError(f"exécution {run_id} toujours en cours")


def settings_of(client: TestClient, task_id: str) -> dict:
    session = client.app.state.session_factory()
    try:
        return bisync_settings(session.get(Task, task_id))
    finally:
        session.close()


# -- verrou de livraison ------------------------------------------------------


def test_the_mode_is_closed_by_default(client: TestClient, tmp_path: Path, local_root: Path) -> None:
    """§21 — reporté plutôt que livré avec un risque de perte de données.

    Le client ordinaire n'active pas le drapeau : c'est la configuration que
    reçoivent les utilisateurs.
    """
    task_id = make_bisync_task(client, local_root / "local", tmp_path / "cloud")
    refused = run(client, task_id)
    assert refused.status_code == 409
    assert "pas encore disponible" in refused.json()["detail"]


# -- protocole ----------------------------------------------------------------


def test_an_uninitialised_task_refuses_to_run(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Le message doit nommer l'action qui débloque, pas constater l'échec."""
    task_id = make_bisync_task(ouvert, local_root / "local", tmp_path / "cloud")
    refused = run(ouvert, task_id)
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert "initialis" in detail
    assert "ré-initialisation" in detail


def test_applying_the_initialisation_requires_simulating_it_first(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """§8.1 — une fusion des deux côtés se lit avant de s'appliquer."""
    task_id = make_bisync_task(ouvert, local_root / "local", tmp_path / "cloud")
    refused = run(ouvert, task_id, dry_run=False, resync=True)
    assert refused.status_code == 409
    assert "simulez" in refused.json()["detail"].lower()


def test_resync_is_refused_on_a_one_way_task(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    from tests.test_tasks_api import make_cloud, make_task

    remote_id = make_cloud(ouvert, tmp_path / "ailleurs")
    task = make_task(ouvert, remote_id, local_root, name="Simple copie")

    refused = run(ouvert, task["id"], resync=True)
    assert refused.status_code == 409
    assert "bidirectionnelles" in refused.json()["detail"]


def test_the_simulation_opens_the_right_to_apply(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    (local / "a.txt").write_text("a", encoding="utf-8")

    started = run(ouvert, task_id, resync=True)
    assert started.status_code == 202, started.text
    assert wait(ouvert, started.json()["id"])["status"] == "success"

    assert settings_of(ouvert, task_id)["resync_simulated"] is True
    assert settings_of(ouvert, task_id)["initialised"] is False
    assert not (cloud / "a.txt").exists(), "une simulation n'écrit rien"


def test_a_real_initialisation_establishes_the_reference(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    (local / "a.txt").write_text("a", encoding="utf-8")
    (cloud / "b.txt").write_text("b", encoding="utf-8")

    wait(ouvert, run(ouvert, task_id, resync=True).json()["id"])
    applied = run(ouvert, task_id, dry_run=False, resync=True)
    assert applied.status_code == 202, applied.text
    assert wait(ouvert, applied.json()["id"])["status"] == "success"

    reglages = settings_of(ouvert, task_id)
    assert reglages["initialised"] is True
    assert reglages["initialised_at"]
    # L'initialisation fusionne : c'est précisément ce qu'il faut avoir lu.
    assert {f.name for f in local.iterdir()} >= {"a.txt", "b.txt"}
    assert {f.name for f in cloud.iterdir()} >= {"a.txt", "b.txt"}


def test_changes_travel_in_both_directions(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Le cœur du mode : ce que les deux sens unidirectionnels ne font pas."""
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    for index in range(8):
        (local / f"f{index}.txt").write_text(str(index), encoding="utf-8")

    wait(ouvert, run(ouvert, task_id, resync=True).json()["id"])
    wait(ouvert, run(ouvert, task_id, dry_run=False, resync=True).json()["id"])

    (local / "depuis-local.txt").write_text("local", encoding="utf-8")
    (cloud / "depuis-cloud.txt").write_text("cloud", encoding="utf-8")

    final = wait(ouvert, run(ouvert, task_id, dry_run=False).json()["id"])
    assert final["status"] == "success", final

    assert (cloud / "depuis-local.txt").exists(), "local → cloud"
    assert (local / "depuis-cloud.txt").exists(), "cloud → local"


def test_lost_listings_put_the_task_back_to_uninitialised(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Code 7 : ce n'est pas une panne, c'est une demande d'action.

    Sans ce retour à l'état non initialisé, l'interface proposerait une
    exécution que bisync refuserait systématiquement.
    """
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    (local / "a.txt").write_text("a", encoding="utf-8")

    wait(ouvert, run(ouvert, task_id, resync=True).json()["id"])
    wait(ouvert, run(ouvert, task_id, dry_run=False, resync=True).json()["id"])
    assert settings_of(ouvert, task_id)["initialised"] is True

    workdir = tmp_path / "config" / "bisync" / task_id
    listings = list(workdir.glob("*.lst"))
    assert listings, "les listings doivent vivre dans l'appdata"
    for listing in listings:
        listing.unlink()

    broken = wait(ouvert, run(ouvert, task_id, dry_run=False).json()["id"])
    assert broken["status"] == "needs_resync"
    assert broken["exit_code"] == 7
    assert settings_of(ouvert, task_id)["initialised"] is False


def test_the_working_directory_lives_in_the_appdata(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Un cache hors volume transformerait chaque mise à jour en fusion."""
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    (local / "a.txt").write_text("a", encoding="utf-8")

    wait(ouvert, run(ouvert, task_id, resync=True).json()["id"])
    assert (tmp_path / "config" / "bisync" / task_id).is_dir()


def test_the_settings_survive_as_json(ouvert: TestClient, tmp_path: Path, local_root: Path) -> None:
    task_id = make_bisync_task(ouvert, local_root / "local", tmp_path / "cloud")
    session = ouvert.app.state.session_factory()
    try:
        task = session.get(Task, task_id)
        task.bisync_json = json.dumps({"conflict_resolve": "newer"})
        session.commit()
    finally:
        session.close()

    reglages = settings_of(ouvert, task_id)
    assert reglages["conflict_resolve"] == "newer"
    assert reglages["initialised"] is False, "les défauts comblent les absences"
