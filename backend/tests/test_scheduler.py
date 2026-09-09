"""Planificateur, historique et tableau de bord (PLAN-002, LOG-005, UI-004).

Le battement de fond est désactivé dans les tests : chaque scénario pilote
son propre planificateur avec une horloge qu'il fait avancer lui-même.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.db.models import Task, TaskRun
from csm.services.scheduler import Scheduler, local_now
from tests.test_tasks_api import make_cloud, make_task, wait_for

pytestmark = pytest.mark.usefixtures("rclone_path")


class FakeClock:
    """Horloge pilotée par le test."""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, **delta: float) -> None:
        self.moment += timedelta(**delta)


def build_scheduler(client: TestClient, clock: FakeClock, **kwargs: object) -> Scheduler:
    return Scheduler(
        client.app.state.session_factory,
        client.app.state.run_manager,
        clock=clock,
        **kwargs,  # type: ignore[arg-type]
    )


def prepare(client: TestClient, tmp_path: Path, local_root: Path, **schedule: object) -> dict:
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Photos"
    source.mkdir(parents=True, exist_ok=True)
    (source / "a.txt").write_text("un", encoding="utf-8")
    task = make_task(client, remote_id, source)
    if schedule:
        response = client.patch(f"/api/tasks/{task['id']}", json={"schedule": schedule})
        assert response.status_code == 200, response.text
        return response.json()
    return task


# -- planification -----------------------------------------------------------


def test_a_schedule_is_described_and_dated(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    task = prepare(client, tmp_path, local_root, kind="interval", minutes=30)
    assert task["schedule_label"] == "toutes les 30 minutes"
    assert task["next_run_at"] is not None
    assert task["status"] == "scheduled"


def test_an_invalid_schedule_is_refused(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    task = prepare(client, tmp_path, local_root)
    response = client.patch(
        f"/api/tasks/{task['id']}", json={"schedule": {"kind": "weekly", "time": "02:30"}}
    )
    assert response.status_code == 409
    assert "jour" in response.json()["detail"]


def test_the_scheduler_starts_a_task_when_due(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    cloud = tmp_path / "cloud"
    task = prepare(client, tmp_path, local_root, kind="interval", minutes=60)
    clock = FakeClock(local_now())
    scheduler = build_scheduler(client, clock)

    assert scheduler.tick() == [], "rien n'est dû à l'instant même"

    clock.advance(minutes=61)
    started = scheduler.tick()
    assert len(started) == 1

    run = wait_for(client, started[0])
    assert run["status"] == "success"
    assert run["dry_run"] is False, "une exécution planifiée n'est pas une simulation"
    assert (cloud / "a.txt").exists()

    refreshed = client.get("/api/tasks").json()[0]
    assert refreshed["next_run_at"] is not None
    assert scheduler.tick() == [], "l'échéance a bien été avancée"


def test_a_disabled_task_is_never_started(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    task = prepare(client, tmp_path, local_root, kind="interval", minutes=60)
    paused = client.patch(f"/api/tasks/{task['id']}", json={"enabled": False}).json()
    assert paused["status"] == "paused"

    clock = FakeClock(local_now())
    clock.advance(hours=5)
    assert build_scheduler(client, clock).tick() == []


def test_an_overrunning_task_does_not_stack(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """§11 — une exécution qui déborde ne doit pas en déclencher une seconde."""
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Gros"
    source.mkdir()
    (source / "gros.bin").write_bytes(b"0" * (2 * 1024 * 1024))
    task = make_task(client, remote_id, source)
    client.patch(f"/api/tasks/{task['id']}", json={"schedule": {"kind": "interval", "minutes": 1}})

    from tests.test_mirror_guards import configure

    configure(client, task["id"], bandwidth_json='{"limit": "20k"}')

    clock = FakeClock(local_now())
    scheduler = build_scheduler(client, clock)
    clock.advance(minutes=2)

    first = scheduler.tick()
    assert len(first) == 1

    clock.advance(minutes=2)
    assert scheduler.tick() == [], "une seconde exécution a été empilée"

    client.post(f"/api/runs/{first[0]}/stop")
    wait_for(client, first[0])


# -- rattrapage au redémarrage (§11) -----------------------------------------


def test_a_missed_deadline_is_not_replayed_by_default(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    task = prepare(client, tmp_path, local_root, kind="interval", minutes=60)
    _force_deadline(client, task["id"], local_now() - timedelta(hours=6))

    clock = FakeClock(local_now())
    scheduler = build_scheduler(client, clock)

    assert scheduler.prime() == 0
    assert scheduler.tick() == [], "une occurrence manquée a été rejouée"

    refreshed = client.get("/api/tasks").json()[0]
    # L'API rend des dates avec fuseau : la comparaison doit rester en aware,
    # sinon on retombe exactement dans le piège que corrige UtcDateTime.
    assert datetime.fromisoformat(refreshed["next_run_at"]) > clock.moment


def test_a_missed_deadline_is_replayed_once_when_asked(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    task = prepare(
        client, tmp_path, local_root, kind="interval", minutes=60, catch_up="once"
    )
    _force_deadline(client, task["id"], local_now() - timedelta(hours=6))

    clock = FakeClock(local_now())
    scheduler = build_scheduler(client, clock)

    assert scheduler.prime() == 1
    started = scheduler.tick()
    assert len(started) == 1
    wait_for(client, started[0])

    # Une seule fois : le tour suivant ne relance rien.
    assert scheduler.tick() == []


# -- rétention de l'historique (LOG-005) -------------------------------------


def test_history_retention_keeps_the_last_runs(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    task = prepare(client, tmp_path, local_root)
    now = local_now()

    session = client.app.state.session_factory()
    try:
        for age_days in (400, 300, 200, 100, 1):
            session.add(
                TaskRun(
                    task_id=task["id"],
                    status="success",
                    started_at=now - timedelta(days=age_days),
                    ended_at=now - timedelta(days=age_days),
                )
            )
        session.commit()
    finally:
        session.close()

    scheduler = build_scheduler(
        client, FakeClock(now), history_retention_days=90, history_keep_runs=3
    )
    assert scheduler.purge_history() == 2

    remaining = client.get(f"/api/tasks/{task['id']}/runs").json()
    assert len(remaining) == 3


def test_recent_history_is_never_purged(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    task = prepare(client, tmp_path, local_root)
    now = local_now()

    session = client.app.state.session_factory()
    try:
        for index in range(6):
            session.add(
                TaskRun(
                    task_id=task["id"],
                    status="success",
                    started_at=now - timedelta(days=index),
                )
            )
        session.commit()
    finally:
        session.close()

    scheduler = build_scheduler(
        client, FakeClock(now), history_retention_days=90, history_keep_runs=2
    )
    assert scheduler.purge_history() == 0
    assert len(client.get(f"/api/tasks/{task['id']}/runs").json()) == 6


# -- tableau de bord (UI-001, UI-004) ----------------------------------------


def test_dashboard_summarises_everything_in_one_call(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    task = prepare(client, tmp_path, local_root, kind="interval", minutes=45)

    payload = client.get("/api/dashboard").json()
    assert payload["counters"]["total"] == 1
    assert payload["counters"]["scheduled"] == 1
    assert payload["next_run_at"] is not None
    assert payload["running"] == []
    assert payload["recent_runs"] == []

    clock = FakeClock(local_now())
    clock.advance(minutes=46)
    started = build_scheduler(client, clock).tick()
    wait_for(client, started[0])

    after = client.get("/api/dashboard").json()
    assert len(after["recent_runs"]) == 1
    assert after["recent_runs"][0]["status"] == "success"


def test_dashboard_counts_a_paused_task(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    task = prepare(client, tmp_path, local_root, kind="interval", minutes=45)
    client.patch(f"/api/tasks/{task['id']}", json={"enabled": False})

    counters = client.get("/api/dashboard").json()["counters"]
    assert counters["paused"] == 1
    assert counters["scheduled"] == 0


def _force_deadline(client: TestClient, task_id: str, moment: datetime) -> None:
    session = client.app.state.session_factory()
    try:
        session.get(Task, task_id).next_run_at = moment
        session.commit()
    finally:
        session.close()
