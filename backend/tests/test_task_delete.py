"""Suppression d'une tâche (TASK-005, §10.4).

Supprimer une tâche ne supprime aucun fichier de l'utilisateur. En revanche
l'application laissait derrière elle ses propres fichiers de travail, qui
s'accumulaient dans l'appdata sans que rien ne les réclame jamais.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.db.models import FilterSet, Task
from tests.test_tasks_api import make_cloud, make_task

pytestmark = pytest.mark.usefixtures("rclone_path")


def mirror_with_filters(
    client: TestClient, tmp_path: Path, local_root: Path
) -> tuple[str, Path]:
    """Une tâche Miroir dotée d'un jeu de filtres matérialisé."""
    cloud = tmp_path / "cloud"
    source = local_root / "source"
    source.mkdir(parents=True, exist_ok=True)
    (source / "a.txt").write_text("a", encoding="utf-8")

    remote_id = make_cloud(client, cloud)
    task = make_task(client, remote_id, source, name="À supprimer", mode="mirror")

    session = client.app.state.session_factory()
    try:
        jeu = FilterSet(
            name="Exclure les temporaires",
            rules_json=json.dumps([{"type": "exclude_extension", "value": "tmp"}]),
        )
        session.add(jeu)
        session.flush()
        session.get(Task, task["id"]).filter_set_id = jeu.id
        session.commit()
    finally:
        session.close()

    return task["id"], cloud


def test_deleting_removes_the_task(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    task_id, _ = mirror_with_filters(client, tmp_path, local_root)
    assert client.delete(f"/api/tasks/{task_id}").status_code == 204
    restantes = {t["id"] for t in client.get("/api/tasks").json()}
    assert task_id not in restantes


def test_deleting_takes_the_working_files_with_it(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Sans ce ménage, l'appdata se remplissait de répertoires orphelins."""
    task_id, _ = mirror_with_filters(client, tmp_path, local_root)
    config = tmp_path / "config"

    # Ce que l'application aurait déposé au fil des exécutions.
    filtre = config / "filters" / f"{task_id}.filter"
    filtre.parent.mkdir(parents=True, exist_ok=True)
    filtre.write_text("- *.tmp\n", encoding="utf-8")

    workdir = config / "bisync" / task_id
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "path1.lst").write_text("listing", encoding="utf-8")

    assert client.delete(f"/api/tasks/{task_id}").status_code == 204
    assert not filtre.exists()
    assert not workdir.exists()


def test_deleting_never_touches_the_user_files(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """La crainte naturelle est infondée, et doit le rester."""
    task_id, cloud = mirror_with_filters(client, tmp_path, local_root)
    corbeille = cloud / ".cloudsync-trash"
    corbeille.mkdir(parents=True, exist_ok=True)
    (corbeille / "ancien.txt").write_text("récupérable", encoding="utf-8")

    client.delete(f"/api/tasks/{task_id}")

    assert (local_root / "source" / "a.txt").exists()
    assert (corbeille / "ancien.txt").exists()


def test_a_running_task_is_not_deletable(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    task_id, _ = mirror_with_filters(client, tmp_path, local_root)
    session = client.app.state.session_factory()
    try:
        session.get(Task, task_id).status = "running"
        session.commit()
    finally:
        session.close()

    runner = client.app.state.run_manager
    started = runner.start(task_id, dry_run=True)
    refused = client.delete(f"/api/tasks/{task_id}")
    runner.stop(started)

    assert refused.status_code == 409
    assert "arrêtez-la" in refused.json()["detail"]


def test_the_filter_set_itself_survives(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Un jeu de filtres peut servir à plusieurs tâches : le détruire avec
    l'une d'elles en priverait les autres."""
    task_id, _ = mirror_with_filters(client, tmp_path, local_root)
    session = client.app.state.session_factory()
    try:
        filter_set_id = session.get(Task, task_id).filter_set_id
    finally:
        session.close()

    client.delete(f"/api/tasks/{task_id}")

    session = client.app.state.session_factory()
    try:
        assert session.get(FilterSet, filter_set_id) is not None
    finally:
        session.close()


def test_forgetting_a_workspace_is_idempotent(
    client: TestClient, tmp_path: Path
) -> None:
    """Une seconde suppression ne doit pas échouer sur des fichiers absents."""
    from csm.services.tasks import forget_workspace

    settings = client.app.state.settings
    assert forget_workspace(settings, "tache-inexistante") == []
