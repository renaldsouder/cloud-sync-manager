"""Cycle de vie du registre d'exécutions.

Ces cas n'étaient couverts par aucun test : ils portent sur ce que l'API
raconte à l'interface, pas sur ce que la base contient. C'est l'audit du
contrat avec le frontend qui les a fait apparaître.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.db.models import Task, TaskRun
from csm.services.runner import mark_orphan_runs_interrupted
from tests.test_tasks_api import make_cloud, make_task, start, wait_for

pytestmark = pytest.mark.usefixtures("rclone_path")


def test_a_finished_run_stops_being_announced_as_live(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Sinon l'interface affiche « En cours » indéfiniment, avec un bouton
    « Arrêter » qui ne sert plus à rien — et le registre ne se vide jamais."""
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Photos"
    source.mkdir()
    (source / "a.txt").write_text("un", encoding="utf-8")

    task = make_task(client, remote_id, source)
    run = wait_for(client, start(client, task["id"], dry_run=False))
    assert run["status"] == "success"

    assert client.get("/api/tasks").json()[0]["live"] is None
    assert client.get(f"/api/runs/{run['id']}").json()["live"] is None
    assert client.app.state.run_manager.snapshots() == []


def test_the_registry_does_not_grow_with_each_run(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """§13 — aucune croissance illimitée en mémoire."""
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Photos"
    source.mkdir()
    (source / "a.txt").write_text("un", encoding="utf-8")
    task = make_task(client, remote_id, source)

    for _ in range(3):
        wait_for(client, start(client, task["id"], dry_run=True))

    assert client.app.state.run_manager.snapshots() == []
    assert len(client.get(f"/api/tasks/{task['id']}/runs").json()) == 3


def test_a_run_cut_by_a_container_stop_is_marked_interrupted(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """§8.5 — après un arrêt forcé ou un plantage, jamais « Réussie ».

    Sans ce rattrapage au démarrage, l'exécution resterait « en cours » pour
    toujours et la tâche refuserait tout nouveau lancement.
    """
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Photos"
    source.mkdir()
    task = make_task(client, remote_id, source)

    factory = client.app.state.session_factory
    session = factory()
    try:
        orphan = TaskRun(task_id=task["id"], status="running", dry_run=False)
        session.add(orphan)
        session.get(Task, task["id"]).status = "running"
        session.commit()
        orphan_id = orphan.id
    finally:
        session.close()

    assert mark_orphan_runs_interrupted(factory) == 1

    recovered = client.get(f"/api/runs/{orphan_id}").json()
    assert recovered["status"] == "interrupted"
    assert recovered["status"] != "success"
    assert recovered["ended_at"] is not None
    assert client.get("/api/tasks").json()[0]["status"] == "ready"

    # La tâche doit redevenir lançable.
    assert client.post(f"/api/tasks/{task['id']}/run", json={"dry_run": True}).status_code == 202


def test_orphan_recovery_leaves_finished_runs_alone(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Photos"
    source.mkdir()
    (source / "a.txt").write_text("un", encoding="utf-8")
    task = make_task(client, remote_id, source)

    run = wait_for(client, start(client, task["id"], dry_run=False))
    assert mark_orphan_runs_interrupted(client.app.state.session_factory) == 0
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "success"
