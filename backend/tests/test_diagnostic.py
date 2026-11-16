"""Package de diagnostic (LOG-004, DATA-005, SEC-002).

Ce fichier finit le plus souvent sur un forum public. Ce qui y fuit est
perdu pour de bon : ces tests le remplissent de tout ce qui ne doit pas
en sortir, puis vérifient que rien n'en sort.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.db.models import TaskEvent, TaskRun
from csm.services.diagnostic import Anonymiser
from tests.test_tasks_api import make_cloud, make_task

SFTP_SECRET = "Sup3r-S3cret-SFTP!"
UNRAID_KEY = "cle-api-unraid-0123456789"
WEBHOOK_TOKEN = "AbCdEf1234567890webhooktoken"
BEARER = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJyZW5hbGQifQ.c2lnbmF0dXJl"


def _populate(client: TestClient, tmp_path: Path, local_root: Path) -> dict:
    remote_id = make_cloud(client, tmp_path / "cloud", name="OneDrive de Dupont")
    response = client.post(
        "/api/remotes",
        json={
            "name": "NAS du bureau",
            "provider": "sftp",
            "options": {"host": "192.168.1.10", "user": "dupont", "pass": SFTP_SECRET},
        },
    )
    assert response.status_code == 201, response.text
    client.put(
        "/api/settings",
        json={
            "unraid_url": "https://tour-dupont.local",
            "unraid_api_key": UNRAID_KEY,
            "webhook_url": f"https://discord.com/api/webhooks/123/{WEBHOOK_TOKEN}",
        },
    )
    source = local_root / "Photos Famille Dupont" / "Vacances"
    source.mkdir(parents=True)
    task = make_task(
        client,
        remote_id,
        source,
        name="Photos de la famille Dupont",
        remote_path="Archives Dupont/2026",
    )

    session = client.app.state.session_factory()
    try:
        run = TaskRun(task_id=task["id"], status="error", dry_run=False, exit_code=5)
        session.add(run)
        session.flush()
        session.add_all(
            [
                TaskEvent(
                    run_id=run.id,
                    kind="error",
                    path="Vacances/Mariage Dupont.jpg",
                    message=(
                        "Failed to copy Mariage Dupont.jpg: 401 "
                        f"Authorization: Bearer {BEARER}"
                    ),
                ),
                TaskEvent(
                    run_id=run.id,
                    kind="transfer",
                    path="Vacances/plage.jpg",
                    message="Copied (new)",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()
    return task


def _download(client: TestClient, **params: str) -> tuple[str, dict]:
    response = client.get("/api/diagnostic", params=params)
    assert response.status_code == 200, response.text
    assert "attachment" in response.headers["content-disposition"]
    return response.text, response.json()


@pytest.mark.usefixtures("rclone_path")
def test_no_secret_ever_leaves_even_without_anonymisation(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """DATA-005 — l'anonymisation est un choix ; le masquage des secrets
    n'en est pas un."""
    _populate(client, tmp_path, local_root)
    for params in ({}, {"anonymise": "false"}):
        text, payload = _download(client, **params)
        for secret in (SFTP_SECRET, UNRAID_KEY, WEBHOOK_TOKEN, BEARER):
            assert secret not in text, f"{secret} a fui ({params})"
        assert payload["preferences"]["notifications"]["webhook"] == "configuré"
        assert payload["preferences"]["notifications"]["unraid_api_key"] == "configuré"


@pytest.mark.usefixtures("rclone_path")
def test_anonymised_package_names_nobody(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    _populate(client, tmp_path, local_root)
    text, payload = _download(client)

    assert payload["anonymised"] is True
    for personal in ("Dupont", "Mariage", "Vacances", "bureau", "tour-dupont"):
        assert personal not in text, f"« {personal} » apparaît dans le diagnostic"

    task = payload["tasks"][0]
    # La racine du montage est une convention Unraid, pas une donnée
    # personnelle : elle reste, et situe le chemin.
    assert task["local_path"].startswith(str(local_root))
    event = next(e for e in task["runs"][0]["events"] if e["kind"] == "error")
    assert event["path"].endswith(".jpg"), "l'extension aide à diagnostiquer un filtre"
    assert "401" in event["message"], "la cause technique doit rester lisible"
    assert event["path"].split("/")[-1] in event["message"]


@pytest.mark.usefixtures("rclone_path")
def test_the_package_keeps_what_support_needs(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    _populate(client, tmp_path, local_root)
    _, payload = _download(client)

    assert payload["versions"]["application"]
    assert payload["versions"]["rclone"]
    assert {r["provider"] for r in payload["remotes"]} == {"alias", "sftp"}
    run = payload["tasks"][0]["runs"][0]
    assert run["status"] == "error" and run["exit_code"] == 5
    # Seuls les événements qui expliquent un échec sont relevés.
    assert [e["kind"] for e in run["events"]] == ["error"]


@pytest.mark.usefixtures("rclone_path")
def test_without_anonymisation_names_are_kept(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    _populate(client, tmp_path, local_root)
    _, payload = _download(client, anonymise="false")
    assert payload["tasks"][0]["name"] == "Photos de la famille Dupont"


def test_tokens_are_stable_so_shared_folders_stay_visible() -> None:
    """Deux tâches qui partagent un dossier doivent encore le partager dans
    le diagnostic : c'est souvent la cause d'un chevauchement."""
    names = Anonymiser(True, ("/mnt/user",))
    first = names.local_path("/mnt/user/Photos/2025")
    second = names.local_path("/mnt/user/Photos/2026")
    assert first.split("/")[3] == second.split("/")[3]
    assert first != second
    assert names.local_path("/mnt/user") == "/mnt/user"


def test_messages_lose_known_names_but_keep_their_meaning() -> None:
    names = Anonymiser(True, ("/mnt/user",))
    names.local_path("/mnt/user/Documents Martin")
    message = names.text("directory not found: /mnt/user/Documents Martin")
    assert "Martin" not in message
    assert message.startswith("directory not found: /mnt/user/")
