"""Sauvegarde, restauration et notifications (DATA-002/003/005, NOTIF-001/004)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.services.notifications import (
    DEFAULT_EVENTS,
    NotificationConfig,
    Notifier,
    event_for,
    summarise_run,
)
from tests.test_tasks_api import make_cloud, make_task

pytestmark = pytest.mark.usefixtures("rclone_path")

SFTP_SECRET = "MotDePasseSFTP!42"


# -- notifications -----------------------------------------------------------


def test_nothing_is_sent_without_configuration() -> None:
    assert Notifier(NotificationConfig()).notify("failure", "sujet", "détail") == []


def test_only_the_selected_events_are_sent() -> None:
    config = NotificationConfig(webhook_url="http://exemple.invalide/hook", events=("blocked",))
    assert config.wants("blocked") is True
    assert config.wants("failure") is False


def test_an_unreachable_channel_never_raises() -> None:
    """Une synchronisation ne doit jamais échouer parce qu'un webhook est tombé."""
    config = NotificationConfig(webhook_url="http://127.0.0.1:9/hook")
    results = Notifier(config).notify("failure", "sujet", "détail")
    assert [result.ok for result in results] == [False]
    assert results[0].detail, "l'échec doit porter une cause exploitable"


def test_a_self_signed_certificate_is_explained_not_just_refused() -> None:
    """§27.10 — l'utilisateur ne doit pas fouiller les journaux du conteneur.

    Un serveur Unraid en HTTPS présente par défaut un certificat auto-signé :
    c'est le cas le plus courant, il mérite un message qui dit quoi faire.
    """
    from ssl import SSLCertVerificationError

    from csm.services.notifications import _transport_error

    detail = _transport_error(
        SSLCertVerificationError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
            "self-signed certificate (_ssl.c:1032)"
        )
    )
    assert "auto-signé" in detail
    assert "http://" in detail


def test_transport_errors_are_translated() -> None:
    from csm.services.notifications import _transport_error

    assert "refusée" in _transport_error(OSError("Connection refused"))
    assert "introuvable" in _transport_error(OSError("Name or service not known"))
    assert "injoignable" in _transport_error(OSError("operation timed out"))


def test_permission_errors_name_the_missing_scope() -> None:
    import urllib.error

    from csm.services.notifications import _http_error

    detail = _http_error(
        urllib.error.HTTPError("http://x/graphql", 403, "Forbidden", {}, None)
    )
    assert "NOTIFICATIONS:CREATE_ANY" in detail


def test_graphql_errors_keep_the_server_message() -> None:
    from csm.services.notifications import _graphql_error

    detail = _graphql_error(
        '{"errors":[{"message":"Cannot query field notifyIfUnique"}]}'
    )
    assert detail == "Cannot query field notifyIfUnique"


def test_self_signed_option_round_trips(client: TestClient) -> None:
    saved = client.put("/api/settings", json={"allow_self_signed": True})
    assert saved.status_code == 200
    assert client.get("/api/settings").json()["notifications"]["allow_self_signed"] is True

    client.put("/api/settings", json={"allow_self_signed": False})
    assert (
        client.get("/api/settings").json()["notifications"]["allow_self_signed"] is False
    )


def test_the_test_endpoint_returns_the_cause(client: TestClient) -> None:
    client.put("/api/settings", json={"webhook_url": "http://127.0.0.1:9/hook"})
    payload = client.post("/api/settings/notifications/test").json()

    assert payload["delivered"] is False
    assert payload["detail"], "le motif doit remonter jusqu'à l'interface"
    assert payload["results"][0]["channel"] == "Webhook"
    assert payload["results"][0]["ok"] is False


def test_event_mapping() -> None:
    assert event_for("error") == "failure"
    assert event_for("warning") == "failure"
    assert event_for("blocked") == "blocked"
    assert event_for("success") == "success"
    assert event_for("interrupted") is None


def test_wording_names_the_task() -> None:
    subject, description = summarise_run("Photos", "blocked", "12 suppressions prévues")
    assert "Photos" in subject
    assert "validation" in subject
    assert description == "12 suppressions prévues"


def test_the_api_key_is_never_returned(client: TestClient) -> None:
    saved = client.put(
        "/api/settings",
        json={
            "unraid_url": "http://192.168.1.10",
            "unraid_api_key": "cle-tres-secrete",
            "events": ["failure", "blocked"],
        },
    )
    assert saved.status_code == 200, saved.text

    payload = client.get("/api/settings").json()["notifications"]
    assert payload["unraid_api_key_configured"] is True
    assert "cle-tres-secrete" not in json.dumps(payload)
    assert payload["unraid_url"] == "http://192.168.1.10"


def test_saving_without_retyping_the_key_keeps_it(client: TestClient) -> None:
    client.put(
        "/api/settings",
        json={"unraid_url": "http://192.168.1.10", "unraid_api_key": "cle"},
    )
    client.put("/api/settings", json={"webhook_url": "http://exemple.invalide/hook"})

    payload = client.get("/api/settings").json()["notifications"]
    assert payload["unraid_api_key_configured"] is True
    assert payload["webhook_url"] == "http://exemple.invalide/hook"


def test_unknown_event_is_refused(client: TestClient) -> None:
    refused = client.put("/api/settings", json={"events": ["tout"]})
    assert refused.status_code == 409
    assert "inconnu" in refused.json()["detail"]


def test_defaults_are_actionable_only(client: TestClient) -> None:
    """§15 — des alertes utiles, pas du bruit : pas de succès par défaut."""
    payload = client.get("/api/settings").json()["notifications"]
    assert payload["events"] == list(DEFAULT_EVENTS)
    assert "success" not in payload["events"]


def test_testing_without_a_channel_is_refused(client: TestClient) -> None:
    refused = client.post("/api/settings/notifications/test")
    assert refused.status_code == 409


# -- export / import ---------------------------------------------------------


def _populate(client: TestClient, tmp_path: Path, local_root: Path) -> None:
    remote_id = make_cloud(client, tmp_path / "cloud")
    client.post(
        "/api/remotes",
        json={
            "name": "NAS distant",
            "provider": "sftp",
            "options": {"host": "192.168.1.10", "user": "renald", "pass": SFTP_SECRET},
        },
    )
    filter_id = client.post(
        "/api/filter-sets",
        json={"name": "Sans tmp", "rules": [{"type": "exclude_ext", "value": "tmp"}]},
    ).json()["id"]

    source = local_root / "Photos"
    source.mkdir()
    task = make_task(client, remote_id, source)
    client.patch(
        f"/api/tasks/{task['id']}",
        json={
            "filter_set_id": filter_id,
            "schedule": {"kind": "daily", "time": "02:30", "catch_up": "skip"},
        },
    )


def test_export_contains_no_secret(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """DATA-005 — une sauvegarde finit sur une clé USB, pas dans un coffre."""
    _populate(client, tmp_path, local_root)
    response = client.get("/api/config/export")
    assert response.status_code == 200

    assert SFTP_SECRET not in response.text
    payload = response.json()
    assert payload["contains_secrets"] is False
    assert payload["format_version"] == 1
    assert {remote["name"] for remote in payload["remotes"]} == {
        "Cloud simulé",
        "NAS distant",
    }
    assert all("options" not in remote for remote in payload["remotes"])
    assert payload["tasks"][0]["schedule"]["time"] == "02:30"


def test_export_omits_the_notification_key(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    client.put("/api/settings", json={"unraid_api_key": "cle-tres-secrete"})
    assert "cle-tres-secrete" not in client.get("/api/config/export").text


def test_import_restores_everything_but_starts_nothing(
    client: TestClient, tmp_path: Path, local_root: Path, settings
) -> None:
    """§16 — une restauration ne lance aucune tâche avant validation."""
    _populate(client, tmp_path, local_root)
    exported = client.get("/api/config/export").json()

    # Une base neuve, pour restaurer sur un serveur vierge.
    from csm.config import Settings
    from csm.main import create_app

    fresh = Settings(
        config_dir=tmp_path / "config2",
        web_dir=tmp_path / "no-web",
        allowed_roots=str(local_root),
        rclone_binary=settings.rclone_binary,
        scheduler_enabled=False,
    )
    with TestClient(create_app(fresh)) as restored:
        report = restored.post("/api/config/import", json=exported)
        assert report.status_code == 200, report.text
        body = report.json()
        assert body["remotes_created"] == 2
        assert body["filter_sets_created"] == 1
        assert body["tasks_created"] == 1
        assert any("réauthentifié" in warning for warning in body["warnings"])

        tasks = restored.get("/api/tasks").json()
        assert len(tasks) == 1
        assert tasks[0]["enabled"] is False, "une tâche importée ne doit pas démarrer"
        assert tasks[0]["status"] == "paused"
        assert tasks[0]["schedule"]["time"] == "02:30"

        # Les stockages sont là mais sans identifiants : à retester.
        assert all(
            remote["status"] == "unknown" for remote in restored.get("/api/remotes").json()
        )


def test_import_is_idempotent(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    _populate(client, tmp_path, local_root)
    exported = client.get("/api/config/export").json()

    again = client.post("/api/config/import", json=exported).json()
    assert again["tasks_created"] == 0
    assert again["remotes_created"] == 0
    assert any("déjà présente" in item for item in again["skipped"])


def test_a_newer_format_is_refused(client: TestClient) -> None:
    """UPDATE-002 — mieux vaut refuser que restaurer de travers."""
    refused = client.post("/api/config/import", json={"format_version": 99})
    assert refused.status_code == 409
    assert "mettez l'application à jour" in refused.json()["detail"]


def test_an_unreadable_backup_is_refused(client: TestClient) -> None:
    assert client.post("/api/config/import", json={"tasks": []}).status_code == 409
