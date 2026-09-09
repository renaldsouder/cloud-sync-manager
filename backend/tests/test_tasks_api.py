"""Tâches de bout en bout, contre le vrai rclone (SYNC-001/002/004, TASK-002)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("rclone_path")


def make_cloud(client: TestClient, target: Path, name: str = "Cloud simulé") -> str:
    target.mkdir(parents=True, exist_ok=True)
    response = client.post(
        "/api/remotes",
        json={"name": name, "provider": "alias", "options": {"remote": str(target)}},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def make_task(
    client: TestClient,
    remote_id: str,
    local_path: Path,
    *,
    name: str = "Photos vers Cloud",
    direction: str = "local_to_remote",
    mode: str = "copy",
    remote_path: str = "",
) -> dict:
    response = client.post(
        "/api/tasks",
        json={
            "name": name,
            "remote_id": remote_id,
            "local_path": str(local_path),
            "remote_path": remote_path,
            "direction": direction,
            "mode": mode,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def wait_for(client: TestClient, run_id: str, timeout: float = 90.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] != "running":
            return run
        time.sleep(0.1)
    raise AssertionError(f"exécution {run_id} toujours en cours après {timeout} s")


def start(client: TestClient, task_id: str, *, dry_run: bool) -> str:
    response = client.post(f"/api/tasks/{task_id}/run", json={"dry_run": dry_run})
    assert response.status_code == 202, response.text
    return response.json()["id"]


# -- configuration ----------------------------------------------------------


def test_local_path_must_stay_inside_the_allowed_roots(
    client: TestClient, tmp_path: Path
) -> None:
    """LOCAL-005 — refus à la création, pas à l'exécution."""
    remote_id = make_cloud(client, tmp_path / "cloud")
    response = client.post(
        "/api/tasks",
        json={
            "name": "Évasion",
            "remote_id": remote_id,
            "local_path": str(tmp_path / "ailleurs"),
            "remote_path": "",
            "direction": "local_to_remote",
            "mode": "copy",
        },
    )
    assert response.status_code == 409
    assert "racines autorisées" in response.json()["detail"]


def test_protected_share_refused_as_destination(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """P13 — un Cloud → Local ne peut pas viser appdata."""
    remote_id = make_cloud(client, tmp_path / "cloud")
    (local_root / "appdata").mkdir()
    response = client.post(
        "/api/tasks",
        json={
            "name": "Descente dangereuse",
            "remote_id": remote_id,
            "local_path": str(local_root / "appdata"),
            "remote_path": "",
            "direction": "remote_to_local",
            "mode": "copy",
        },
    )
    assert response.status_code == 409
    assert "appdata" in response.json()["detail"]


def test_overlapping_tasks_are_refused(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    remote_id = make_cloud(client, tmp_path / "cloud")
    parent = local_root / "Documents"
    (parent / "Factures").mkdir(parents=True)
    make_task(client, remote_id, parent, name="Documents")

    response = client.post(
        "/api/tasks",
        json={
            "name": "Factures",
            "remote_id": remote_id,
            "local_path": str(parent / "Factures"),
            "remote_path": "",
            "direction": "local_to_remote",
            "mode": "copy",
        },
    )
    assert response.status_code == 409
    assert "recoupe" in response.json()["detail"]


def test_mirror_defaults_are_protective(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """CONF-002 et §8.1 — un Miroir naît protégé, sans réglage de l'utilisateur."""
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Photos"
    source.mkdir()
    task = make_task(client, remote_id, source, mode="mirror")

    assert task["mode"] == "mirror"
    assert task["dry_run_required"] is True
    assert task["delete_policy"] == "confirm"
    assert task["quarantine_enabled"] is True

    refused = client.post(f"/api/tasks/{task['id']}/run", json={"dry_run": False})
    assert refused.status_code == 409
    assert "simulation" in refused.json()["detail"]


def test_cloud_to_local_mirror_gets_stricter_thresholds(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """P13 — descendre vers le share de l'utilisateur mérite plus de méfiance."""
    from csm.db.models import Task

    remote_id = make_cloud(client, tmp_path / "cloud")
    for direction, folder in (("local_to_remote", "Montant"), ("remote_to_local", "Descendant")):
        target = local_root / folder
        target.mkdir()
        make_task(
            client,
            remote_id,
            target,
            name=f"Miroir {folder}",
            direction=direction,
            mode="mirror",
        )

    session = client.app.state.session_factory()
    try:
        seuils = {
            task.direction: (task.max_deletes, task.max_delete_percent)
            for task in session.query(Task).all()
        }
    finally:
        session.close()

    assert seuils["local_to_remote"] == (100, 10)
    assert seuils["remote_to_local"] == (25, 5)


# -- exécution --------------------------------------------------------------


def test_dry_run_changes_nothing_on_disk(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    cloud = tmp_path / "cloud"
    remote_id = make_cloud(client, cloud)
    source = local_root / "Photos"
    source.mkdir()
    (source / "a.txt").write_text("un", encoding="utf-8")

    task = make_task(client, remote_id, source)
    run = wait_for(client, start(client, task["id"], dry_run=True))

    assert run["status"] == "success"
    assert run["dry_run"] is True
    assert list(cloud.iterdir()) == [], "la simulation a écrit sur le disque"

    events = client.get(f"/api/runs/{run['id']}/events").json()
    assert any(event["kind"] == "skip_transfer" for event in events)
    assert any(event["path"] == "a.txt" for event in events)


def test_real_copy_transfers_and_records(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    cloud = tmp_path / "cloud"
    remote_id = make_cloud(client, cloud)
    source = local_root / "Photos"
    (source / "sous").mkdir(parents=True)
    (source / "a.txt").write_text("un", encoding="utf-8")
    (source / "sous" / "b éà.txt").write_text("deux", encoding="utf-8")

    task = make_task(client, remote_id, source)
    run = wait_for(client, start(client, task["id"], dry_run=False))

    assert run["status"] == "success", run
    assert run["exit_code"] == 0
    assert run["transferred_files"] == 2
    assert run["transferred_bytes"] == 6
    assert run["deleted_files"] == 0
    assert run["rclone_version"].startswith("v")

    # GLOBAL-005 : l'accentué doit survivre au transfert et au journal.
    assert (cloud / "a.txt").read_text(encoding="utf-8") == "un"
    assert (cloud / "sous" / "b éà.txt").read_text(encoding="utf-8") == "deux"

    events = client.get(f"/api/runs/{run['id']}/events").json()
    paths = {event["path"] for event in events if event["kind"] == "transfer"}
    assert paths == {"a.txt", "sous/b éà.txt"}


def test_copy_never_deletes_at_destination(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """§7.1 — la Copie n'enlève rien à destination, c'est son contrat."""
    cloud = tmp_path / "cloud"
    cloud.mkdir()
    (cloud / "deja-la.txt").write_text("garde-moi", encoding="utf-8")
    remote_id = make_cloud(client, cloud)

    source = local_root / "Photos"
    source.mkdir()
    (source / "a.txt").write_text("un", encoding="utf-8")

    task = make_task(client, remote_id, source)
    run = wait_for(client, start(client, task["id"], dry_run=False))

    assert run["status"] == "success"
    assert run["deleted_files"] == 0
    assert (cloud / "deja-la.txt").read_text(encoding="utf-8") == "garde-moi"


def test_cloud_to_local_direction(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """SYNC-002 — le sens descendant écrit dans le share."""
    cloud = tmp_path / "cloud"
    (cloud / "Photos").mkdir(parents=True)
    (cloud / "Photos" / "vacances.jpg").write_bytes(b"jpeg")
    remote_id = make_cloud(client, cloud)

    destination = local_root / "Recuperation"
    destination.mkdir()
    task = make_task(
        client,
        remote_id,
        destination,
        name="Cloud vers local",
        direction="remote_to_local",
        remote_path="Photos",
    )
    run = wait_for(client, start(client, task["id"], dry_run=False))

    assert run["status"] == "success", run
    assert (destination / "vacances.jpg").read_bytes() == b"jpeg"


def test_history_and_task_status(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """LOG-001 — chaque exécution laisse une trace exploitable."""
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Photos"
    source.mkdir()
    (source / "a.txt").write_text("un", encoding="utf-8")

    task = make_task(client, remote_id, source)
    wait_for(client, start(client, task["id"], dry_run=True))
    wait_for(client, start(client, task["id"], dry_run=False))

    runs = client.get(f"/api/tasks/{task['id']}/runs").json()
    assert len(runs) == 2
    assert {run["dry_run"] for run in runs} == {True, False}
    assert all(run["ended_at"] for run in runs)

    refreshed = client.get("/api/tasks").json()[0]
    assert refreshed["status"] == "success"
    assert refreshed["last_run_id"] == runs[0]["id"]


def test_a_task_cannot_run_twice_at_once(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Gros"
    source.mkdir()
    (source / "gros.bin").write_bytes(b"0" * (2 * 1024 * 1024))

    task = make_task(client, remote_id, source)
    _throttle(client, task["id"], "20k")

    run_id = start(client, task["id"], dry_run=False)
    second = client.post(f"/api/tasks/{task['id']}/run", json={"dry_run": False})
    assert second.status_code == 409

    client.post(f"/api/runs/{run_id}/stop")
    wait_for(client, run_id)


def test_stop_marks_the_run_interrupted(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """TASK-002 et §8.5 — un arrêt ne produit jamais « Réussie »."""
    cloud = tmp_path / "cloud"
    remote_id = make_cloud(client, cloud)
    source = local_root / "Gros"
    source.mkdir()
    (source / "gros.bin").write_bytes(b"0" * (4 * 1024 * 1024))

    task = make_task(client, remote_id, source)
    _throttle(client, task["id"], "20k")  # ~200 s de transfert : marge confortable

    run_id = start(client, task["id"], dry_run=False)
    _wait_until_started(client, run_id)

    stopped = client.post(f"/api/runs/{run_id}/stop")
    assert stopped.status_code == 200

    run = wait_for(client, run_id, timeout=60)
    assert run["status"] == "interrupted"
    assert run["status"] != "success"

    assert client.get("/api/tasks").json()[0]["status"] == "ready"


def test_stopping_a_finished_run_is_refused(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Photos"
    source.mkdir()
    (source / "a.txt").write_text("un", encoding="utf-8")

    task = make_task(client, remote_id, source)
    run_id = start(client, task["id"], dry_run=False)
    wait_for(client, run_id)

    assert client.post(f"/api/runs/{run_id}/stop").status_code == 409


def test_unknown_ids_return_404(client: TestClient) -> None:
    assert client.get("/api/runs/inconnu").status_code == 404
    assert client.get("/api/tasks/inconnu/runs").status_code == 404
    assert client.delete("/api/tasks/inconnu").status_code == 404


# -- utilitaires ------------------------------------------------------------


def _throttle(client: TestClient, task_id: str, limit: str) -> None:
    """Limite le débit pour rendre les tests d'arrêt déterministes."""
    factory = client.app.state.session_factory
    from csm.db.models import Task

    session = factory()
    try:
        task = session.get(Task, task_id)
        task.bandwidth_json = json.dumps({"limit": limit})
        session.commit()
    finally:
        session.close()


def _wait_until_started(client: TestClient, run_id: str, timeout: float = 20.0) -> None:
    """Attend que rclone ait réellement commencé à transférer."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        live = run.get("live") or {}
        if live.get("stats", {}).get("bytes", 0) > 0 or live.get("current_file"):
            return
        if run["status"] != "running":
            return
        time.sleep(0.1)
