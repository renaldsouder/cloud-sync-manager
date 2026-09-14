"""Assistant de première utilisation (FIRST-001, §3).

L'état est déduit du contenu de l'application, jamais d'un compteur qu'on
incrémenterait : une étape doit se décocher si l'on supprime ce qu'elle avait
produit, sinon l'assistant donnerait une piste fausse au moment où
l'utilisateur en a le plus besoin.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.db.models import Remote, Task, TaskRun
from tests.test_tasks_api import make_cloud, make_task

pytestmark = pytest.mark.usefixtures("rclone_path")


def etat(client: TestClient) -> dict:
    reponse = client.get("/api/onboarding")
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


def test_everything_is_pending_on_a_fresh_install(client: TestClient) -> None:
    depart = etat(client)
    assert depart == {
        "password": False,
        "remote": False,
        "remote_tested": False,
        "task": False,
        "simulation": False,
        "dismissed": False,
        "completed": False,
    }


def test_adding_a_storage_ticks_only_its_own_step(
    client: TestClient, tmp_path: Path
) -> None:
    """Un stockage ajouté n'est pas un stockage éprouvé (§20.4)."""
    make_cloud(client, tmp_path / "cloud")

    apres = etat(client)
    assert apres["remote"] is True
    assert apres["remote_tested"] is False, "ajouter n'est pas tester"
    assert apres["completed"] is False


def test_testing_the_storage_ticks_the_step(
    client: TestClient, tmp_path: Path
) -> None:
    remote_id = make_cloud(client, tmp_path / "cloud")
    assert client.post(f"/api/remotes/{remote_id}/test").status_code == 200
    assert etat(client)["remote_tested"] is True


def test_a_task_ticks_its_step(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "source"
    source.mkdir(parents=True, exist_ok=True)
    make_task(client, remote_id, source, name="Première", mode="copy")

    apres = etat(client)
    assert apres["task"] is True
    assert apres["simulation"] is False


def test_only_a_successful_simulation_counts(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """§8.1 — une simulation en échec n'autorise rien."""
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "source"
    source.mkdir(parents=True, exist_ok=True)
    task = make_task(client, remote_id, source, name="Première", mode="copy")

    session = client.app.state.session_factory()
    try:
        session.add(TaskRun(task_id=task["id"], status="error", dry_run=True))
        session.commit()
    finally:
        session.close()
    assert etat(client)["simulation"] is False

    session = client.app.state.session_factory()
    try:
        session.add(TaskRun(task_id=task["id"], status="success", dry_run=True))
        session.commit()
    finally:
        session.close()
    assert etat(client)["simulation"] is True


def test_a_real_run_is_not_a_simulation(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "source"
    source.mkdir(parents=True, exist_ok=True)
    task = make_task(client, remote_id, source, name="Première", mode="copy")

    session = client.app.state.session_factory()
    try:
        session.add(TaskRun(task_id=task["id"], status="success", dry_run=False))
        session.commit()
    finally:
        session.close()

    assert etat(client)["simulation"] is False


def test_a_step_unticks_when_what_it_produced_disappears(
    client: TestClient, tmp_path: Path
) -> None:
    """Le point qui justifie de déduire l'état plutôt que de le mémoriser."""
    remote_id = make_cloud(client, tmp_path / "cloud")
    client.post(f"/api/remotes/{remote_id}/test")
    assert etat(client)["remote_tested"] is True

    assert client.delete(f"/api/remotes/{remote_id}").status_code == 204
    apres = etat(client)
    assert apres["remote"] is False
    assert apres["remote_tested"] is False


def test_the_password_step_follows_the_real_setting(client: TestClient) -> None:
    assert etat(client)["password"] is False
    pose = client.put("/api/auth/password", json={"new_password": "un mot de passe"})
    assert pose.status_code == 200, pose.text
    assert etat(client)["password"] is True


def test_dismissing_survives(client: TestClient) -> None:
    """L'assistant est une aide : insister en ferait une gêne."""
    assert etat(client)["dismissed"] is False

    ignore = client.post("/api/onboarding/dismiss")
    assert ignore.status_code == 200, ignore.text
    assert ignore.json()["dismissed"] is True
    assert etat(client)["dismissed"] is True


def test_the_journey_completes(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Le parcours du §3, de bout en bout."""
    client.put("/api/auth/password", json={"new_password": "un mot de passe"})

    remote_id = make_cloud(client, tmp_path / "cloud")
    client.post(f"/api/remotes/{remote_id}/test")

    source = local_root / "source"
    source.mkdir(parents=True, exist_ok=True)
    (source / "a.txt").write_text("a", encoding="utf-8")
    task = make_task(client, remote_id, source, name="Première", mode="copy")

    session = client.app.state.session_factory()
    try:
        session.add(TaskRun(task_id=task["id"], status="success", dry_run=True))
        session.commit()
    finally:
        session.close()

    final = etat(client)
    assert final["completed"] is True, final


def test_an_untested_storage_blocks_completion(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Un stockage jamais éprouvé ne doit pas laisser croire que tout va bien."""
    client.put("/api/auth/password", json={"new_password": "un mot de passe"})
    remote_id = make_cloud(client, tmp_path / "cloud")

    source = local_root / "source"
    source.mkdir(parents=True, exist_ok=True)
    task = make_task(client, remote_id, source, name="Première", mode="copy")

    session = client.app.state.session_factory()
    try:
        session.add(TaskRun(task_id=task["id"], status="success", dry_run=True))
        session.commit()
    finally:
        session.close()

    assert etat(client)["completed"] is False


def test_a_broken_storage_does_not_count_as_tested(
    client: TestClient, tmp_path: Path
) -> None:
    make_cloud(client, tmp_path / "cloud")
    session = client.app.state.session_factory()
    try:
        for remote in session.query(Remote).all():
            remote.status = "error"
        session.commit()
    finally:
        session.close()

    assert etat(client)["remote_tested"] is False


def test_deleting_the_task_unticks_it(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "source"
    source.mkdir(parents=True, exist_ok=True)
    task = make_task(client, remote_id, source, name="Première", mode="copy")
    assert etat(client)["task"] is True

    session = client.app.state.session_factory()
    try:
        session.delete(session.get(Task, task["id"]))
        session.commit()
    finally:
        session.close()

    assert etat(client)["task"] is False
