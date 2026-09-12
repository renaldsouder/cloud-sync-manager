"""Modification d'une tâche existante (TASK-001, §8.1, §8.3).

Créer une tâche était possible, la modifier ne l'était pas : un nom mal
choisi ou un seuil mal réglé condamnait à tout recréer. Ces tests couvrent
la moitié manquante, et surtout ce qui doit rester interdit.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.db.models import Task
from tests.test_tasks_api import make_cloud, make_task

pytestmark = pytest.mark.usefixtures("rclone_path")


def stored(client: TestClient, task_id: str) -> Task:
    session = client.app.state.session_factory()
    try:
        return session.get(Task, task_id)
    finally:
        session.close()


def mirror(client: TestClient, tmp_path: Path, local_root: Path) -> dict:
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "source"
    source.mkdir(parents=True, exist_ok=True)
    return make_task(client, remote_id, source, name="Miroir", mode="mirror")


# -- ce qui devient modifiable ------------------------------------------------


def test_the_name_can_be_changed(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    task = mirror(client, tmp_path, local_root)
    updated = client.patch(f"/api/tasks/{task['id']}", json={"name": "Nouveau nom"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Nouveau nom"


def test_the_thresholds_can_be_changed(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """§8.3 les veut configurables : ils ne l'étaient par aucun chemin."""
    task = mirror(client, tmp_path, local_root)
    updated = client.patch(
        f"/api/tasks/{task['id']}",
        json={"max_deletes": 7, "max_delete_percent": 42},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["max_deletes"] == 7
    assert updated.json()["max_delete_percent"] == 42


def test_a_threshold_of_zero_is_accepted(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Zéro désactive le critère : ce n'est pas une valeur manquante."""
    task = mirror(client, tmp_path, local_root)
    updated = client.patch(f"/api/tasks/{task['id']}", json={"max_delete_percent": 0})
    assert updated.status_code == 200, updated.text
    assert stored(client, task["id"]).max_delete_percent == 0


def test_the_filter_set_can_be_detached(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    task = mirror(client, tmp_path, local_root)
    updated = client.patch(f"/api/tasks/{task['id']}", json={"filter_set_id": None})
    assert updated.status_code == 200, updated.text
    assert stored(client, task["id"]).filter_set_id is None


# -- ce qui reste interdit ----------------------------------------------------


@pytest.mark.parametrize("champ", ["mode", "direction"])
def test_the_mode_and_direction_are_refused_with_a_reason(
    client: TestClient, tmp_path: Path, local_root: Path, champ: str
) -> None:
    """Le refus doit expliquer, pas seulement refuser (§27.10).

    Auparavant ces champs étaient acceptés avec un 200 puis ignorés :
    l'appelant croyait sa modification appliquée.
    """
    task = mirror(client, tmp_path, local_root)
    valeur = "copy" if champ == "mode" else "remote_to_local"

    refused = client.patch(f"/api/tasks/{task['id']}", json={champ: valeur})
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert "§8.1" in detail or "simulation" in detail

    assert getattr(stored(client, task["id"]), champ) != valeur


def test_an_unknown_field_is_refused_instead_of_ignored(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Un champ mal orthographié doit se voir, pas disparaître en silence."""
    task = mirror(client, tmp_path, local_root)
    refused = client.patch(f"/api/tasks/{task['id']}", json={"max_delete": 3})
    assert refused.status_code == 422


def test_a_duplicate_name_is_refused(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    remote_id = make_cloud(client, tmp_path / "cloud")
    for nom in ("un", "deux"):
        (local_root / nom).mkdir(parents=True, exist_ok=True)
    premiere = make_task(
        client, remote_id, local_root / "un", name="Première", mode="copy"
    )
    make_task(client, remote_id, local_root / "deux", name="Seconde", mode="copy")

    refused = client.patch(f"/api/tasks/{premiere['id']}", json={"name": "Seconde"})
    assert refused.status_code == 409


def test_a_path_outside_the_allowed_roots_is_refused(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """LOCAL-005 — la modification ne doit pas être une porte dérobée."""
    task = mirror(client, tmp_path, local_root)
    refused = client.patch(
        f"/api/tasks/{task['id']}", json={"local_path": str(tmp_path / "ailleurs")}
    )
    assert refused.status_code == 409
    assert "racines autoris" in refused.json()["detail"]


# -- §8.1 : ce qui réarme la simulation ---------------------------------------


def test_changing_a_path_rearms_the_simulation(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    task = mirror(client, tmp_path, local_root)
    session = client.app.state.session_factory()
    try:
        session.get(Task, task["id"]).dry_run_required = False
        session.commit()
    finally:
        session.close()

    ailleurs = local_root / "autre-source"
    ailleurs.mkdir(parents=True, exist_ok=True)
    client.patch(f"/api/tasks/{task['id']}", json={"local_path": str(ailleurs)})

    assert stored(client, task["id"]).dry_run_required is True


def test_removing_the_trash_rearms_the_simulation(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Sans corbeille, les suppressions deviennent définitives : le
    changement mérite d'être simulé avant de s'appliquer."""
    task = mirror(client, tmp_path, local_root)
    session = client.app.state.session_factory()
    try:
        session.get(Task, task["id"]).dry_run_required = False
        session.commit()
    finally:
        session.close()

    client.patch(f"/api/tasks/{task['id']}", json={"quarantine_enabled": False})

    stocke = stored(client, task["id"])
    assert stocke.quarantine_enabled is False
    assert stocke.dry_run_required is True


def test_renaming_does_not_rearm_the_simulation(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Réarmer sans raison découragerait l'usage du garde-fou."""
    task = mirror(client, tmp_path, local_root)
    session = client.app.state.session_factory()
    try:
        session.get(Task, task["id"]).dry_run_required = False
        session.commit()
    finally:
        session.close()

    client.patch(f"/api/tasks/{task['id']}", json={"name": "Autre nom"})
    assert stored(client, task["id"]).dry_run_required is False
