"""Copie ponctuelle, sans tâche permanente (SYNC-008).

Une copie ponctuelle se lance en passant, sans la relire comme on relit une
tâche. Ces tests vérifient qu'elle ne peut donc jamais rien supprimer, et
qu'elle ne s'immisce dans rien de ce qui protège les tâches permanentes :
liste, export, planificateur, contrôle de chevauchement.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.db.models import Task
from csm.services.scheduler import Scheduler, local_now
from tests.test_scheduler import FakeClock
from tests.test_tasks_api import make_cloud, make_task, wait_for

pytestmark = pytest.mark.usefixtures("rclone_path")


def _copy(client: TestClient, remote_id: str, local: Path, **extra: object) -> dict:
    response = client.post(
        "/api/one-shot-copies",
        json={"remote_id": remote_id, "local_path": str(local), **extra},
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_a_one_shot_copy_transfers_and_is_listed_apart(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    cloud = tmp_path / "cloud"
    remote_id = make_cloud(client, cloud)
    source = local_root / "Photos"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")

    run = wait_for(client, _copy(client, remote_id, source, remote_path="Archive")["id"])
    assert run["status"] == "success"
    assert (cloud / "Archive" / "a.txt").read_text(encoding="utf-8") == "a"

    # Elle n'encombre ni la liste des tâches ni le tableau de bord…
    assert client.get("/api/tasks").json() == []
    # … mais on la retrouve dans les copies récentes, avec son résultat.
    recent = client.get("/api/one-shot-copies").json()
    assert len(recent) == 1
    assert recent[0]["task"]["mode"] == "copy"
    assert recent[0]["task"]["name"].startswith("Copie ponctuelle du ")
    assert recent[0]["run"]["status"] == "success"


def test_a_one_shot_copy_never_deletes(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Le sens du ticket : une opération lancée sans relecture ne doit pas
    pouvoir supprimer. Un ``mode`` glissé dans la requête est refusé, pas
    ignoré, et un fichier absent de la source reste à destination."""
    cloud = tmp_path / "cloud"
    remote_id = make_cloud(client, cloud)
    (cloud / "garder.txt").write_text("garder", encoding="utf-8")
    source = local_root / "Photos"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")

    for extra in ({"mode": "mirror"}, {"delete_policy": "propagate"}):
        response = client.post(
            "/api/one-shot-copies",
            json={"remote_id": remote_id, "local_path": str(source), **extra},
        )
        assert response.status_code == 422, extra

    run = wait_for(client, _copy(client, remote_id, source)["id"])
    assert run["status"] == "success"
    assert (cloud / "garder.txt").exists()


def test_paths_are_checked_like_a_task(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """LOCAL-005 — une copie ponctuelle n'est pas un passe-droit."""
    remote_id = make_cloud(client, tmp_path / "cloud")
    for local in ("/etc", f"{local_root}/../escape"):
        response = client.post(
            "/api/one-shot-copies", json={"remote_id": remote_id, "local_path": local}
        )
        assert response.status_code == 409, local
    response = client.post(
        "/api/one-shot-copies",
        json={"remote_id": remote_id, "local_path": str(local_root), "remote_path": "../x"},
    )
    assert response.status_code == 409
    assert client.get("/api/one-shot-copies").json() == []


def test_a_copy_that_cannot_start_leaves_no_trace(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    remote_id = make_cloud(client, tmp_path / "cloud")
    response = client.post(
        "/api/one-shot-copies",
        json={"remote_id": remote_id, "local_path": str(local_root / "absent")},
    )
    assert response.status_code == 409
    assert client.get("/api/one-shot-copies").json() == []


def test_a_one_shot_copy_stays_out_of_permanent_tasks(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Elle ne bloque pas la création d'une tâche sur le même dossier, ne
    part pas dans un export, ne se modifie pas, et ne retient pas le
    stockage qu'elle a utilisé."""
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Photos"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")
    copy_run = wait_for(client, _copy(client, remote_id, source)["id"])
    copy_task_id = copy_run["task_id"]

    make_task(client, remote_id, source)
    assert len(client.get("/api/config/export").json()["tasks"]) == 1

    response = client.patch(f"/api/tasks/{copy_task_id}", json={"name": "renommée"})
    assert response.status_code == 409

    # Le stockage reste lié à la tâche permanente…
    assert client.delete(f"/api/remotes/{remote_id}").status_code == 409
    for task in client.get("/api/tasks").json():
        assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    # … mais pas à la copie ponctuelle, qui part avec lui.
    assert client.delete(f"/api/remotes/{remote_id}").status_code == 204
    assert client.get("/api/one-shot-copies").json() == []


def test_old_one_shot_copies_are_purged(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Photos"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")
    old_id = wait_for(client, _copy(client, remote_id, source)["id"])["task_id"]
    wait_for(client, _copy(client, remote_id, source)["id"])

    now = local_now()
    session = client.app.state.session_factory()
    try:
        old = session.get(Task, old_id)
        assert old is not None
        old.created_at = now - timedelta(days=200)
        session.commit()
    finally:
        session.close()

    scheduler = Scheduler(
        client.app.state.session_factory,
        client.app.state.run_manager,
        clock=FakeClock(now),
        history_retention_days=90,
    )
    assert scheduler.purge_history() == 1
    remaining = client.get("/api/one-shot-copies").json()
    assert len(remaining) == 1
    assert remaining[0]["task"]["id"] != old_id
