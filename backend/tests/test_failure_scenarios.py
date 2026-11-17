"""Pannes de l'environnement (GLOBAL-006).

Quatre situations que le cahier des charges demande d'éprouver : perte
réseau, redémarrage du conteneur, quota distant atteint, autorisation
expirée. Pour chacune, deux questions : l'application dit-elle la vérité
sur ce qui s'est passé, et laisse-t-elle les données intactes ?

La perte réseau est réelle — un stockage WebDAV sur un port fermé, joint
par le vrai rclone. Le quota et l'autorisation, qu'aucun stockage local ne
sait produire, sont simulés au niveau du processus avec les messages que
rclone rapporte réellement pour Google Drive, OneDrive et Dropbox.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.config import Settings
from csm.main import create_app
from csm.services import notifications
from csm.services.runner import failure_cause
from tests.test_mirror_guards import configure
from tests.test_run_retry import photos, settle, wait_for_runs
from tests.test_tasks_api import make_cloud, make_task, start, wait_for

pytestmark = pytest.mark.usefixtures("rclone_path")

FAST_RETRIES = "0.005,0.005"

#: Messages relevés dans les journaux de rclone.
DRIVE_QUOTA = (
    "Failed to copy: googleapi: Error 403: The user's Drive storage quota has "
    "been exceeded., storageQuotaExceeded"
)
DROPBOX_QUOTA = "Failed to copy: upload failed: path/insufficient_space/"
ONEDRIVE_QUOTA = "Failed to copy: insufficientStorage: Insufficient Storage"
EXPIRED_TOKEN = (
    "Failed to create file system: couldn't fetch token: invalid_grant: "
    "maybe token is expired or revoked - try \"rclone config reconnect\""
)
DROPBOX_EXPIRED = "Failed to copy: expired_access_token/"


@pytest.fixture
def retry_client(settings: Settings) -> Iterator[TestClient]:
    tuned = settings.model_copy(update={"retry_delays_minutes": FAST_RETRIES})
    with TestClient(create_app(tuned)) as test_client:
        yield test_client


def inject(client: TestClient, message: str, *, exit_code: int, count: int = 1) -> list:
    """Les ``count`` prochains transferts réels échouent avec ``message``."""
    adapter = client.app.state.run_manager._adapter
    real_start = adapter.start
    launched: list[list[str]] = []
    remaining = {"failures": count}

    def fake_start(arguments: list[str]) -> subprocess.Popen[str]:
        transfer = arguments[0] in {"copy", "sync"} and "--dry-run" not in arguments
        if transfer:
            launched.append(list(arguments))
        if transfer and remaining["failures"] > 0:
            remaining["failures"] -= 1
            line = json.dumps({"level": "error", "msg": message})
            script = f"import sys; sys.stderr.write({line!r} + '\\n'); sys.exit({exit_code})"
            return subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
        return real_start(arguments)

    adapter.start = fake_start  # type: ignore[method-assign]
    return launched


def capture_notifications(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> list[tuple[str, str, str]]:
    """Active un canal fictif et relève chaque alerte envoyée."""
    sent: list[tuple[str, str, str]] = []

    class Recorder:
        def __init__(self, config: object) -> None:
            pass

        def notify(self, event: str, subject: str, description: str) -> list:
            sent.append((event, subject, description))
            return []

    from csm.services import runner as runner_module

    monkeypatch.setattr(runner_module.notifications, "Notifier", Recorder)
    monkeypatch.setattr(
        runner_module.settings_store,
        "notification_config",
        lambda session: notifications.NotificationConfig(
            webhook_url="http://exemple.invalid/hook",
            events=notifications.EVENTS,
        ),
    )
    return sent


def closed_port() -> int:
    """Un port local sur lequel rien n'écoute : la connexion est refusée."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# -- classement des causes ---------------------------------------------------


@pytest.mark.parametrize(
    ("message", "cause"),
    [
        (DRIVE_QUOTA, "quota"),
        (DROPBOX_QUOTA, "quota"),
        (ONEDRIVE_QUOTA, "quota"),
        ("write /mnt/user/x: no space left on device", "quota"),
        (EXPIRED_TOKEN, "auth"),
        (DROPBOX_EXPIRED, "auth"),
        ("HTTP error 401 (401 Unauthorized) returned body", "auth"),
        ("connexion réinitialisée par le serveur", None),
        ("Failed to copy: token expiré.txt: permission denied", None),
        (None, None),
    ],
)
def test_the_cause_is_recognised(message: str | None, cause: str | None) -> None:
    assert failure_cause(message) == cause


# -- perte réseau --------------------------------------------------------------


def unreachable_remote(client: TestClient) -> str:
    response = client.post(
        "/api/remotes",
        json={
            "name": "Hors réseau",
            "provider": "webdav",
            "options": {"url": f"http://127.0.0.1:{closed_port()}", "vendor": "other"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_a_lost_network_fails_a_copy_without_touching_the_source(
    client: TestClient, local_root: Path
) -> None:
    source = photos(local_root, "a.txt", "b.txt")
    task = make_task(client, unreachable_remote(client), source)

    run = wait_for(client, start(client, task["id"], dry_run=False))

    assert run["status"] == "error", "jamais « Réussie » sans destination"
    assert sorted(p.name for p in source.iterdir()) == ["a.txt", "b.txt"]


def test_a_lost_network_never_empties_a_local_mirror(
    client: TestClient, local_root: Path
) -> None:
    """Cloud → Local en Miroir, le Cloud injoignable : la source ne peut pas
    être listée, rien ne doit être supprimé côté local (fail closed)."""
    destination = local_root / "Miroir"
    destination.mkdir()
    for name in ("garder-1.txt", "garder-2.txt"):
        (destination / name).write_text(name, encoding="utf-8")
    task = make_task(
        client,
        unreachable_remote(client),
        destination,
        name="Miroir local",
        direction="remote_to_local",
        mode="mirror",
    )
    configure(client, task["id"], dry_run_required=False)

    run = wait_for(client, start(client, task["id"], dry_run=False))

    assert run["status"] in {"error", "blocked"}
    assert run["deleted_files"] == 0
    assert sorted(p.name for p in destination.iterdir()) == ["garder-1.txt", "garder-2.txt"]


def test_a_network_cut_mid_transfer_is_retried_and_recovers(
    retry_client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Le code 5 de rclone — coupure, délai dépassé — mérite une nouvelle
    tentative, qui rejoue tous les garde-fous."""
    client = retry_client
    cloud = tmp_path / "cloud"
    task = make_task(client, make_cloud(client, cloud), photos(local_root, "a.txt"))
    inject(client, "dial tcp 192.0.2.10:443: connect: network is unreachable", exit_code=5)

    wait_for(client, start(client, task["id"], dry_run=False))
    runs = wait_for_runs(client, task["id"], 2)

    assert [run["status"] for run in runs] == ["error", "success"]
    assert (cloud / "a.txt").exists()


# -- redémarrage du conteneur --------------------------------------------------


def test_a_pending_retry_is_not_announced_after_a_restart(
    settings: Settings, tmp_path: Path, local_root: Path
) -> None:
    """Les tentatives vivent en mémoire. Après un redémarrage, l'interface
    ne doit pas promettre une tentative qui n'aura jamais lieu : la tâche
    reste en erreur, visible comme telle."""
    patient = settings.model_copy(update={"retry_delays_minutes": "60"})
    with TestClient(create_app(patient)) as client:
        task = make_task(client, make_cloud(client, tmp_path / "cloud"), photos(local_root, "a.txt"))
        inject(client, "connexion réinitialisée par le serveur", exit_code=5)
        wait_for(client, start(client, task["id"], dry_run=False))
        before = client.get("/api/tasks").json()[0]
        assert before["retry"] is not None

    with TestClient(create_app(patient)) as restarted:
        after = restarted.get("/api/tasks").json()[0]
        assert after["retry"] is None
        assert after["status"] == "error"


# -- quota distant atteint -----------------------------------------------------


@pytest.mark.parametrize("message", [DRIVE_QUOTA, DROPBOX_QUOTA, ONEDRIVE_QUOTA])
def test_a_full_remote_is_reported_at_once_not_retried(
    retry_client: TestClient,
    tmp_path: Path,
    local_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    """rclone peut rapporter un espace plein avec le code 5 : relancer trois
    fois ne libère rien et retarde l'alerte d'une heure."""
    client = retry_client
    sent = capture_notifications(client, monkeypatch)
    task = make_task(client, make_cloud(client, tmp_path / "cloud"), photos(local_root, "a.txt"))
    launched = inject(client, message, exit_code=5, count=10)

    run = wait_for(client, start(client, task["id"], dry_run=False))
    runs = settle(client, task["id"])

    assert run["status"] == "error"
    assert [r["status"] for r in runs] == ["error"]
    assert len(launched) == 1, "aucune nouvelle tentative"
    assert client.get("/api/tasks").json()[0]["retry"] is None
    assert len(sent) == 1
    event, _subject, description = sent[0]
    assert event == notifications.FAILURE
    assert "espace" in description


# -- autorisation expirée ------------------------------------------------------


@pytest.mark.parametrize(("message", "exit_code"), [(EXPIRED_TOKEN, 1), (DROPBOX_EXPIRED, 5)])
def test_an_expired_authorisation_raises_its_own_alert(
    retry_client: TestClient,
    tmp_path: Path,
    local_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    exit_code: int,
) -> None:
    """L'événement « Authentification expirée » des réglages doit être
    celui qu'on reçoit, avec ce qu'il faut faire — pas un échec générique."""
    client = retry_client
    sent = capture_notifications(client, monkeypatch)
    task = make_task(client, make_cloud(client, tmp_path / "cloud"), photos(local_root, "a.txt"))
    launched = inject(client, message, exit_code=exit_code, count=10)

    wait_for(client, start(client, task["id"], dry_run=False))
    settle(client, task["id"])

    assert len(launched) == 1, "une autorisation expirée ne se corrige pas en attendant"
    assert len(sent) == 1
    event, _subject, description = sent[0]
    assert event == notifications.AUTH
    assert "Ré-autoriser" in description


def test_an_expired_authorisation_on_a_mirror_deletes_nothing(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Le Miroir échoue pendant le transfert : aucune suppression n'est
    comptée, et la destination garde ce qu'elle avait."""
    cloud = tmp_path / "cloud"
    cloud.mkdir()
    (cloud / "ancien.txt").write_text("ancien", encoding="utf-8")
    task = make_task(
        client, make_cloud(client, cloud), photos(local_root, "a.txt"), mode="mirror"
    )
    configure(client, task["id"], dry_run_required=False, max_delete_percent=100)
    inject(client, EXPIRED_TOKEN, exit_code=1)

    run = wait_for(client, start(client, task["id"], dry_run=False))

    assert run["status"] == "error"
    assert run["deleted_files"] == 0
    assert (cloud / "ancien.txt").exists()
