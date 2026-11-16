"""Nouvelle tentative après une erreur temporaire (SYNC-006).

La panne est simulée au niveau du processus : la première exécution réelle
reçoit, à la place de rclone, un processus qui écrit une erreur au format du
journal JSON et sort avec le code 5 — celui que rclone réserve aux erreurs
« qu'une nouvelle tentative pourrait corriger ». Les simulations et les
tentatives suivantes passent par le vrai rclone : c'est ce qui permet de
vérifier qu'une tentative rejoue réellement les garde-fous.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.config import Settings
from csm.main import create_app
from csm.services.runner import parse_retry_delays
from tests.test_tasks_api import make_cloud, make_task, start, wait_for

pytestmark = pytest.mark.usefixtures("rclone_path")

#: Deux tentatives, très rapprochées : 0,005 min ≈ 0,3 s.
FAST_RETRIES = "0.005,0.005"


@pytest.fixture
def retry_client(settings: Settings) -> Iterator[TestClient]:
    tuned = settings.model_copy(update={"retry_delays_minutes": FAST_RETRIES})
    with TestClient(create_app(tuned)) as test_client:
        yield test_client


def inject_failures(
    client: TestClient, count: int, *, exit_code: int = 5
) -> list[list[str]]:
    """Remplace les ``count`` prochains transferts réels par une panne.

    Renvoie la liste des arguments de chaque transfert réel lancé, panne ou
    non : c'est le relevé de ce que le moteur a effectivement tenté.
    """
    adapter = client.app.state.run_manager._adapter
    real_start = adapter.start
    launched: list[list[str]] = []
    remaining = {"failures": count}

    def start(arguments: list[str]) -> subprocess.Popen[str]:
        transfer = arguments[0] in {"copy", "sync"} and "--dry-run" not in arguments
        if transfer:
            launched.append(list(arguments))
        if transfer and remaining["failures"] > 0:
            remaining["failures"] -= 1
            line = json.dumps(
                {"level": "error", "msg": "connexion réinitialisée par le serveur"}
            )
            script = (
                "import sys; "
                f"sys.stderr.write({line!r} + '\\n'); "
                f"sys.exit({exit_code})"
            )
            return subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
        return real_start(arguments)

    adapter.start = start  # type: ignore[method-assign]
    return launched


def runs_of(client: TestClient, task_id: str) -> list[dict]:
    """Exécutions d'une tâche, de la plus ancienne à la plus récente."""
    return list(reversed(client.get(f"/api/tasks/{task_id}/runs").json()))


def wait_for_runs(
    client: TestClient, task_id: str, count: int, timeout: float = 60.0
) -> list[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runs = runs_of(client, task_id)
        if len(runs) >= count and all(run["status"] != "running" for run in runs):
            return runs
        time.sleep(0.1)
    raise AssertionError(f"{count} exécution(s) attendue(s) pour {task_id}")


def settle(client: TestClient, task_id: str, seconds: float = 1.5) -> list[dict]:
    """Laisse passer plus que la temporisation, puis relève l'historique."""
    time.sleep(seconds)
    return wait_for_runs(client, task_id, 1)


def photos(local_root: Path, *names: str) -> Path:
    source = local_root / "Photos"
    source.mkdir(exist_ok=True)
    for name in names:
        (source / name).write_text(name, encoding="utf-8")
    return source


# -- règles de décision -----------------------------------------------------


def test_retry_delays_are_minutes_and_can_be_disabled() -> None:
    assert parse_retry_delays("5,15,45") == (300.0, 900.0, 2700.0)
    assert parse_retry_delays(" 1 , 0.5 ") == (60.0, 30.0)
    assert parse_retry_delays("") == ()


def test_an_unreadable_retry_setting_is_refused() -> None:
    """Un réglage mal compris en silence est un réglage qui ne protège pas."""
    with pytest.raises(ValueError):
        parse_retry_delays("cinq")
    with pytest.raises(ValueError):
        parse_retry_delays("-1")


# -- de bout en bout --------------------------------------------------------


def test_a_temporary_error_is_retried_and_recovers(
    retry_client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Sans nouvelle tentative, une coupure réseau de quelques secondes à
    02:30 laisse la tâche en erreur jusqu'au lendemain."""
    client = retry_client
    cloud = tmp_path / "cloud"
    remote_id = make_cloud(client, cloud)
    task = make_task(client, remote_id, photos(local_root, "a.txt"))
    launched = inject_failures(client, 1)

    first = wait_for(client, start(client, task["id"], dry_run=False))
    assert first["status"] == "error"

    runs = wait_for_runs(client, task["id"], 2)
    assert [run["status"] for run in runs] == ["error", "success"]
    assert (cloud / "a.txt").read_text(encoding="utf-8") == "a.txt"
    assert len(launched) == 2

    events = client.get(f"/api/runs/{first['id']}/events").json()
    retry = [event for event in events if event["kind"] == "retry"]
    assert retry and "nouvelle tentative 1/2" in retry[0]["message"]

    listed = client.get("/api/tasks").json()[0]
    assert listed["status"] == "success"
    assert listed["retry"] is None


def test_retries_stop_after_the_configured_attempts(
    retry_client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Une panne qui dure ne doit pas devenir une boucle infinie."""
    client = retry_client
    remote_id = make_cloud(client, tmp_path / "cloud")
    task = make_task(client, remote_id, photos(local_root, "a.txt"))
    launched = inject_failures(client, 10)

    wait_for(client, start(client, task["id"], dry_run=False))
    runs = wait_for_runs(client, task["id"], 3)
    runs = settle(client, task["id"])

    assert [run["status"] for run in runs] == ["error", "error", "error"]
    assert len(launched) == 3
    assert client.get("/api/tasks").json()[0]["retry"] is None


def test_the_final_failure_is_notified_once(
    retry_client: TestClient,
    tmp_path: Path,
    local_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une alerte par tentative ferait sonner trois fois pour une seule
    panne ; aucune alerte du tout la ferait passer inaperçue."""
    client = retry_client
    manager = client.app.state.run_manager
    sent: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        manager, "_notify", lambda live, status, detail: sent.append((status, detail))
    )
    remote_id = make_cloud(client, tmp_path / "cloud")
    task = make_task(client, remote_id, photos(local_root, "a.txt"))
    inject_failures(client, 10)

    wait_for(client, start(client, task["id"], dry_run=False))
    wait_for_runs(client, task["id"], 3)
    settle(client, task["id"])

    assert len(sent) == 1
    status, detail = sent[0]
    assert status == "error"
    assert detail is not None and "3 tentatives" in detail


def test_a_permanent_error_is_not_retried(
    retry_client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Un chemin introuvable ou une erreur fatale ne se corrige pas en
    attendant : la relancer masquerait la panne au lieu de la signaler."""
    client = retry_client
    remote_id = make_cloud(client, tmp_path / "cloud")
    task = make_task(client, remote_id, photos(local_root, "a.txt"))
    launched = inject_failures(client, 1, exit_code=1)

    wait_for(client, start(client, task["id"], dry_run=False))
    runs = settle(client, task["id"])

    assert [run["status"] for run in runs] == ["error"]
    assert len(launched) == 1


def test_a_simulation_is_never_retried(
    retry_client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Une simulation sert à lire un résultat : la rejouer d'elle-même
    produirait un historique que personne n'a demandé."""
    client = retry_client
    remote_id = make_cloud(client, tmp_path / "cloud")
    task = make_task(client, remote_id, photos(local_root, "a.txt"))
    adapter = client.app.state.run_manager._adapter

    def failing(arguments: list[str]) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.exit(5)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

    adapter.start = failing  # type: ignore[method-assign]
    wait_for(client, start(client, task["id"], dry_run=True))
    runs = settle(client, task["id"])
    assert [run["status"] for run in runs] == ["error"]


def test_pausing_the_task_cancels_the_pending_retry(
    settings: Settings, tmp_path: Path, local_root: Path
) -> None:
    """Mettre une tâche en pause doit vouloir dire : plus rien ne part."""
    tuned = settings.model_copy(update={"retry_delays_minutes": "0.02"})
    with TestClient(create_app(tuned)) as client:
        remote_id = make_cloud(client, tmp_path / "cloud")
        task = make_task(client, remote_id, photos(local_root, "a.txt"))
        launched = inject_failures(client, 1)

        wait_for(client, start(client, task["id"], dry_run=False))
        pending = client.get("/api/tasks").json()[0]["retry"]
        assert pending is not None and pending["attempt"] == 1

        response = client.patch(f"/api/tasks/{task['id']}", json={"enabled": False})
        assert response.status_code == 200, response.text
        assert client.get("/api/tasks").json()[0]["retry"] is None

        runs = settle(client, task["id"], seconds=2.5)
        assert [run["status"] for run in runs] == ["error"]
        assert len(launched) == 1


def test_a_manual_run_supersedes_the_pending_retry(
    settings: Settings, tmp_path: Path, local_root: Path
) -> None:
    """Sinon l'utilisateur qui relance à la main verrait partir, quelques
    minutes plus tard, une seconde exécution qu'il n'a pas demandée."""
    tuned = settings.model_copy(update={"retry_delays_minutes": "0.02"})
    with TestClient(create_app(tuned)) as client:
        remote_id = make_cloud(client, tmp_path / "cloud")
        task = make_task(client, remote_id, photos(local_root, "a.txt"))
        launched = inject_failures(client, 1)

        wait_for(client, start(client, task["id"], dry_run=False))
        assert client.get("/api/tasks").json()[0]["retry"] is not None

        manual = wait_for(client, start(client, task["id"], dry_run=False))
        assert manual["status"] == "success"
        assert client.get("/api/tasks").json()[0]["retry"] is None

        runs = settle(client, task["id"], seconds=2.5)
        assert [run["status"] for run in runs] == ["error", "success"]
        assert len(launched) == 2


# -- garde-fous destructifs (§8) --------------------------------------------


def test_a_mirror_retry_replays_the_source_check(
    retry_client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """§8.2 — la tentative n'hérite d'aucune décision de l'exécution qu'elle
    remplace. Si le partage s'est vidé entre-temps — démonté pendant la
    coupure —, elle doit échouer fermée, pas vider le Cloud."""
    client = retry_client
    cloud = tmp_path / "cloud"
    remote_id = make_cloud(client, cloud)
    source = photos(local_root, "a.txt", "b.txt")
    task = make_task(client, remote_id, source, mode="mirror")
    assert wait_for(client, start(client, task["id"], dry_run=True))["status"] == "success"
    assert wait_for(client, start(client, task["id"], dry_run=False))["status"] == "success"
    assert sorted(path.name for path in cloud.iterdir()) == ["a.txt", "b.txt"]

    launched = inject_failures(client, 1)
    (source / "c.txt").write_text("c", encoding="utf-8")
    failed = wait_for(client, start(client, task["id"], dry_run=False))
    assert failed["status"] == "error"

    # Le partage disparaît avant que la tentative ne parte.
    for child in source.iterdir():
        child.unlink()

    runs = wait_for_runs(client, task["id"], 4)
    assert runs[-1]["status"] == "blocked"
    assert len(launched) == 1  # la tentative n'a jamais atteint le transfert
    assert sorted(path.name for path in cloud.iterdir()) == ["a.txt", "b.txt"]


def test_a_mirror_retry_never_inherits_a_deletion_confirmation(
    retry_client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """§8.3 — « Supprimer 423 fichiers » vaut pour l'exécution confirmée,
    pas pour celles qui la suivraient en silence. Une exécution confirmée
    qui échoue n'est pas rejouée : c'est à l'utilisateur de confirmer à
    nouveau, sur un décompte à jour."""
    client = retry_client
    cloud = tmp_path / "cloud"
    remote_id = make_cloud(client, cloud)
    source = photos(local_root, *(f"{index}.txt" for index in range(10)))
    task = make_task(client, remote_id, source, mode="mirror")
    client.patch(f"/api/tasks/{task['id']}", json={"max_deletes": 2})
    wait_for(client, start(client, task["id"], dry_run=True))
    wait_for(client, start(client, task["id"], dry_run=False))

    for index in range(5):
        (source / f"{index}.txt").unlink()
    blocked = wait_for(client, start(client, task["id"], dry_run=False))
    assert blocked["status"] == "blocked"

    launched = inject_failures(client, 1)
    response = client.post(
        f"/api/tasks/{task['id']}/run",
        json={"dry_run": False, "confirm_deletions": True},
    )
    assert response.status_code == 202, response.text
    assert wait_for(client, response.json()["id"])["status"] == "error"

    runs = settle(client, task["id"])
    assert runs[-1]["status"] == "error"
    assert len(launched) == 1
    assert len(list(cloud.glob("*.txt"))) == 10
