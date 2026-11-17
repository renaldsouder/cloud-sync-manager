"""Ce que le fournisseur garde de lui-même (CONF-005).

Information seulement : ces tests vérifient qu'elle suit la configuration
réelle du stockage, qu'elle ne promet rien là où rien n'est connu, et
qu'aucune protection de l'application n'en dépend.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.rclone.adapter import RcloneAdapter
from csm.rclone.versioning import KEPT, PERMANENT, UNKNOWN, describe_versioning
from tests.test_tasks_api import make_cloud, make_task


@pytest.mark.parametrize(
    ("provider", "options", "deleted"),
    [
        ("drive", {}, KEPT),
        ("drive", {"use_trash": "true"}, KEPT),
        ("drive", {"use_trash": "false"}, PERMANENT),
        ("onedrive", {}, KEPT),
        ("onedrive", {"hard_delete": "true"}, PERMANENT),
        ("dropbox", {}, KEPT),
        ("b2", {}, KEPT),
        ("b2", {"hard_delete": "true"}, PERMANENT),
        ("sftp", {}, PERMANENT),
        ("webdav", {}, PERMANENT),
    ],
)
def test_the_description_follows_the_configuration(
    provider: str, options: dict[str, str], deleted: str
) -> None:
    assert describe_versioning(provider, options)["deleted"] == deleted


@pytest.mark.parametrize("provider", ["s3", "alias", "mega", "pcloud", ""])
def test_nothing_is_promised_when_nothing_is_known(provider: str) -> None:
    """« Sans les supposer disponibles partout » : un S3 dépend de son
    bucket, un fournisseur non étudié reste inconnu — jamais protégé."""
    described = describe_versioning(provider, {})
    assert described["deleted"] == UNKNOWN
    assert described["replaced"] == UNKNOWN


@pytest.mark.parametrize(
    ("provider", "option"),
    [("drive", "use_trash"), ("onedrive", "hard_delete"), ("b2", "hard_delete")],
)
def test_the_options_read_exist_in_rclone(
    rclone_path: str, tmp_path: Path, provider: str, option: str
) -> None:
    """Une option renommée par une future version de rclone rendrait la
    description fausse en silence : le moteur embarqué doit la connaître."""
    adapter = RcloneAdapter(binary=rclone_path, config_path=tmp_path / "rclone.conf")
    spec = next(p for p in adapter.providers() if p["Name"] == provider)
    assert option in {o["Name"] for o in spec["Options"]}


@pytest.mark.usefixtures("rclone_path")
def test_the_api_describes_each_storage(client: TestClient, tmp_path: Path) -> None:
    make_cloud(client, tmp_path / "cloud")
    remote = client.get("/api/remotes").json()[0]
    assert remote["versioning"]["deleted"] == UNKNOWN
    assert remote["versioning"]["detail"]


@pytest.mark.usefixtures("rclone_path")
def test_a_versioned_provider_does_not_relax_any_guard(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Un Miroir vers un fournisseur qui garde des versions reste soumis à
    la simulation obligatoire et à la corbeille de l'application."""
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "source"
    source.mkdir(parents=True, exist_ok=True)
    task = make_task(client, remote_id, source, name="Miroir", mode="mirror")
    assert task["dry_run_required"] is True
    assert task["quarantine_enabled"] is True
