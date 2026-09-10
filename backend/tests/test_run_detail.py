"""Détail fichier par fichier d'une exécution (LOG-002, §14).

Les données étaient déjà collectées à chaque exécution : ces tests couvrent
ce qui manquait pour les rendre consultables — recherche, pagination, et
l'aveu de troncature sans lequel une liste incomplète passerait pour
complète.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.db.models import TaskEvent, TaskRun
from csm.services.runner import TRUNCATION_NOTICE
from tests.test_tasks_api import make_cloud, make_task


def seed(client: TestClient, tmp_path: Path, events: list[tuple[str, str | None]]) -> str:
    """Crée une exécution terminée et lui rattache des événements."""
    remote_id = make_cloud(client, tmp_path / "cloud")
    local = tmp_path / "mnt" / "user" / "photos"
    local.mkdir(parents=True, exist_ok=True)
    task = make_task(client, remote_id, local)

    factory = client.app.state.session_factory
    session = factory()
    try:
        run = TaskRun(task_id=task["id"], status="success", dry_run=False)
        session.add(run)
        session.flush()
        for kind, path in events:
            session.add(TaskEvent(run_id=run.id, kind=kind, path=path, size=1))
        session.commit()
        return run.id
    finally:
        session.close()


# -- liste --------------------------------------------------------------------


def test_the_detail_lists_every_file(client: TestClient, tmp_path: Path) -> None:
    run_id = seed(
        client,
        tmp_path,
        [("transfer", "a.txt"), ("transfer", "sous/b éà.txt"), ("delete", "vieux.txt")],
    )
    events = client.get(f"/api/runs/{run_id}/events").json()
    assert {event["path"] for event in events} == {"a.txt", "sous/b éà.txt", "vieux.txt"}


def test_the_kind_filter_still_works(client: TestClient, tmp_path: Path) -> None:
    """Contrat existant : BlockedRun s'en sert pour les suppressions prévues."""
    run_id = seed(client, tmp_path, [("transfer", "a.txt"), ("delete", "vieux.txt")])
    events = client.get(f"/api/runs/{run_id}/events", params={"kind": "delete"}).json()
    assert [event["path"] for event in events] == ["vieux.txt"]


# -- recherche ----------------------------------------------------------------


def test_the_search_narrows_by_path(client: TestClient, tmp_path: Path) -> None:
    run_id = seed(
        client,
        tmp_path,
        [("transfer", "photos/ete.jpg"), ("transfer", "docs/impots.pdf")],
    )
    found = client.get(f"/api/runs/{run_id}/events", params={"q": "photos"}).json()
    assert [event["path"] for event in found] == ["photos/ete.jpg"]


def test_the_search_is_case_insensitive(client: TestClient, tmp_path: Path) -> None:
    run_id = seed(client, tmp_path, [("transfer", "Photos/Ete.JPG")])
    found = client.get(f"/api/runs/{run_id}/events", params={"q": "photos"}).json()
    assert len(found) == 1


@pytest.mark.parametrize(
    ("needle", "expected", "other"),
    [
        # Sans échappement, « % » serait un joker et ramènerait tout.
        ("100%", "100%.txt", "1002.txt"),
        # Sans échappement, « _ » remplacerait n'importe quel caractère.
        ("a_b", "a_b.txt", "axb.txt"),
    ],
)
def test_the_search_treats_wildcards_literally(
    client: TestClient, tmp_path: Path, needle: str, expected: str, other: str
) -> None:
    """Dans un champ de recherche de noms de fichiers, « % » est un caractère."""
    run_id = seed(client, tmp_path, [("transfer", expected), ("transfer", other)])
    found = client.get(f"/api/runs/{run_id}/events", params={"q": needle}).json()
    assert [event["path"] for event in found] == [expected]


def test_an_event_without_a_path_is_not_a_search_hit(
    client: TestClient, tmp_path: Path
) -> None:
    run_id = seed(client, tmp_path, [("warning", None), ("transfer", "a.txt")])
    found = client.get(f"/api/runs/{run_id}/events", params={"q": "a"}).json()
    assert [event["path"] for event in found] == ["a.txt"]


# -- pagination ---------------------------------------------------------------


def test_pagination_walks_the_whole_list(client: TestClient, tmp_path: Path) -> None:
    run_id = seed(client, tmp_path, [("transfer", f"f{i:03d}.txt") for i in range(25)])

    seen: list[str] = []
    for offset in (0, 10, 20):
        page = client.get(
            f"/api/runs/{run_id}/events", params={"offset": offset, "limit": 10}
        ).json()
        seen.extend(event["path"] for event in page)

    assert seen == [f"f{i:03d}.txt" for i in range(25)]
    assert len(seen) == len(set(seen)), "une page ne doit rien répéter"


def test_a_negative_offset_does_not_shift_the_page(
    client: TestClient, tmp_path: Path
) -> None:
    run_id = seed(client, tmp_path, [("transfer", "a.txt"), ("transfer", "b.txt")])
    page = client.get(f"/api/runs/{run_id}/events", params={"offset": -5}).json()
    assert [event["path"] for event in page] == ["a.txt", "b.txt"]


# -- synthèse -----------------------------------------------------------------


def test_the_summary_counts_each_kind(client: TestClient, tmp_path: Path) -> None:
    run_id = seed(
        client,
        tmp_path,
        [("transfer", "a.txt"), ("transfer", "b.txt"), ("delete", "c.txt")],
    )
    summary = client.get(f"/api/runs/{run_id}/events/summary").json()
    assert summary["counts"] == {"transfer": 2, "delete": 1}
    assert summary["total"] == 3
    assert summary["truncated"] is False


def test_the_summary_follows_the_search(client: TestClient, tmp_path: Path) -> None:
    """Les compteurs doivent décrire ce que la recherche a retenu."""
    run_id = seed(
        client,
        tmp_path,
        [("transfer", "photos/a.jpg"), ("transfer", "docs/b.pdf"), ("delete", "photos/c.jpg")],
    )
    summary = client.get(
        f"/api/runs/{run_id}/events/summary", params={"q": "photos"}
    ).json()
    assert summary["counts"] == {"transfer": 1, "delete": 1}
    assert summary["total"] == 2


def test_the_summary_admits_a_truncated_list(client: TestClient, tmp_path: Path) -> None:
    """§8 interdit les silences : une liste tronquée doit le dire.

    Sans ce drapeau, un utilisateur qui ne trouve pas son fichier dans la
    liste conclurait qu'il n'a pas été transféré.
    """
    run_id = seed(client, tmp_path, [("transfer", "a.txt")])

    factory = client.app.state.session_factory
    session = factory()
    try:
        session.add(
            TaskEvent(
                run_id=run_id,
                kind="warning",
                message=f"{TRUNCATION_NOTICE} au-delà de 5000 événements",
            )
        )
        session.commit()
    finally:
        session.close()

    assert client.get(f"/api/runs/{run_id}/events/summary").json()["truncated"] is True


def test_an_ordinary_warning_is_not_a_truncation(
    client: TestClient, tmp_path: Path
) -> None:
    run_id = seed(client, tmp_path, [("warning", None)])
    assert client.get(f"/api/runs/{run_id}/events/summary").json()["truncated"] is False


# -- erreurs ------------------------------------------------------------------


@pytest.mark.parametrize("suffix", ["events", "events/summary"])
def test_an_unknown_run_is_refused(client: TestClient, suffix: str) -> None:
    assert client.get(f"/api/runs/inexistante/{suffix}").status_code == 404
