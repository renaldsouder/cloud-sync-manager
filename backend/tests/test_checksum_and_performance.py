"""Comparaison par somme de contrôle et réglages de transfert (SYNC-005,
PERF-001, PERF-002).

Le risque propre à ``--checksum`` : sans algorithme commun aux deux côtés,
rclone ne compare plus que la taille. Un fichier modifié sans changer de
taille ne serait alors plus jamais recopié. Ces tests vérifient que
l'option n'est appliquée que lorsqu'elle peut réellement fonctionner.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.config import Settings
from csm.main import create_app
from csm.rclone.adapter import RcloneAdapter
from csm.services.performance import (
    PerformanceError,
    common_hashes,
    lenient,
    normalise,
)
from tests.test_tasks_api import make_cloud, make_task, start, wait_for


# -- validation -------------------------------------------------------------


def test_valid_settings_are_kept() -> None:
    assert normalise(
        {"transfers": 8, "checkers": 16, "limit": "2M:1M", "checksum": True}
    ) == {"transfers": 8, "checkers": 16, "limit": "2M:1M", "checksum": True}
    assert normalise({"limit": " 500k "}) == {"limit": "500k"}


def test_a_bandwidth_timetable_is_accepted() -> None:
    """PERF-003 — brider en journée, libérer la nuit."""
    assert normalise({"limit": "08:00,512k   19:00,10M:1M 23:30,off"}) == {
        "limit": "08:00,512k 19:00,10M:1M 23:30,off"
    }
    assert normalise({"limit": "Mon-08:00,1M Sat-00:00,off"}) == {
        "limit": "Mon-08:00,1M Sat-00:00,off"
    }


@pytest.mark.parametrize(
    "limit",
    ["25:00,1M", "08:00,1M --max-delete 0", "Lun-08:00,1M", "08:00,1M,", "08:00,1M 9:00,2M"],
)
def test_an_unreadable_timetable_is_refused(limit: str) -> None:
    with pytest.raises(PerformanceError):
        normalise({"limit": limit})


def test_rclone_accepts_the_timetables_we_accept(rclone_path: str, tmp_path: Path) -> None:
    """La validation ne vaut que si rclone comprend ce qu'elle laisse passer."""
    source = tmp_path / "a"
    source.mkdir()
    (source / "f.txt").write_text("f", encoding="utf-8")
    for limit in ("08:00,512k 19:00,10M:1M 23:30,off", "Mon-08:00,1M Sat-00:00,off"):
        completed = subprocess.run(
            [rclone_path, "copy", str(source), str(tmp_path / "b"), "--bwlimit", limit],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr


def test_empty_settings_collapse_to_nothing() -> None:
    """Un formulaire vidé doit revenir aux défauts de rclone, pas stocker
    une série de champs vides."""
    assert normalise({}) is None
    assert normalise({"limit": "", "transfers": None, "checksum": False}) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"limit": "--delete-before"},
        {"limit": "10M --max-delete 0"},
        {"transfers": 0},
        {"transfers": 500},
        {"transfers": "4"},
        {"transfers": True},
        {"checksum": "oui"},
        {"bwlimit": "1M"},
    ],
)
def test_dangerous_or_unclear_settings_are_refused(payload: dict) -> None:
    """Ces valeurs finissent dans la ligne de commande d'une opération qui
    peut être destructive : une valeur qui ressemble à une option ne doit
    jamais y arriver, et une clé inconnue ne doit pas passer pour appliquée."""
    with pytest.raises(PerformanceError):
        normalise(payload)


def test_unreadable_stored_settings_never_block_a_run() -> None:
    """Une base éditée à la main ne doit pas empêcher de synchroniser : on
    retombe sur les défauts de rclone."""
    assert lenient({"limit": "--delete-before", "transfers": -1}) == {}
    assert lenient("illisible") == {}


def test_common_hashes() -> None:
    assert common_hashes(["md5", "sha1"], ["sha1", "quickxor"]) == ["sha1"]
    assert common_hashes(["md5"], []) == []
    assert common_hashes(None, ["md5"]) == []


def test_checksum_is_one_flag_and_the_simulation_matches(tmp_path: Path) -> None:
    """§20.3 — la simulation emprunte la même commande, à ``--dry-run`` près."""
    adapter = RcloneAdapter(binary="rclone", config_path=tmp_path / "rclone.conf")
    real = adapter.build_transfer_args("sync", "/src", "dst:x", checksum=True)
    simulated = adapter.build_transfer_args(
        "sync", "/src", "dst:x", checksum=True, dry_run=True
    )
    assert "--checksum" in real
    assert sorted(simulated) == sorted(real + ["--dry-run"])
    assert "--checksum" not in adapter.build_transfer_args("sync", "/src", "dst:x")


# -- API ----------------------------------------------------------------------

pytestmark_rclone = pytest.mark.usefixtures("rclone_path")


@pytestmark_rclone
def test_the_api_refuses_an_option_disguised_as_a_limit(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Photos"
    source.mkdir()
    task = make_task(client, remote_id, source)

    response = client.patch(
        f"/api/tasks/{task['id']}", json={"bandwidth": {"limit": "--delete-before"}}
    )
    assert response.status_code == 409
    assert "bande passante" in response.json()["detail"]

    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={"bandwidth": {"transfers": 2, "checksum": True}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["bandwidth"] == {"transfers": 2, "checksum": True}

    cleared = client.patch(f"/api/tasks/{task['id']}", json={"bandwidth": {}})
    assert cleared.json()["bandwidth"] is None


@pytestmark_rclone
def test_testing_a_remote_records_its_checksums(
    client: TestClient, tmp_path: Path
) -> None:
    """§9.3 — « checksum disponible » fait partie de la matrice de capacités."""
    remote_id = make_cloud(client, tmp_path / "cloud")
    result = client.post(f"/api/remotes/{remote_id}/test").json()
    assert result["capabilities"]["checksum"] is True
    assert "md5" in result["capabilities"]["hashes"]


@pytestmark_rclone
def test_an_import_drops_tampered_transfer_settings(
    client: TestClient, tmp_path: Path, local_root: Path, settings: Settings
) -> None:
    """Un export retouché à la main ne doit pas pouvoir glisser une option
    dans la ligne de commande d'une tâche restaurée."""
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Photos"
    source.mkdir()
    make_task(client, remote_id, source)
    exported = client.get("/api/config/export").json()
    exported["tasks"][0]["bandwidth"] = {"limit": "--delete-before"}

    fresh = settings.model_copy(update={"config_dir": tmp_path / "config2"})
    with TestClient(create_app(fresh)) as restored:
        report = restored.post("/api/config/import", json=exported).json()
        assert any("réglages de transfert ignorés" in w for w in report["warnings"])
        assert restored.get("/api/tasks").json()[0]["bandwidth"] is None


# -- de bout en bout ----------------------------------------------------------


def _same_size_same_date(source: Path, destination: Path) -> None:
    """Deux contenus différents, de même taille et de même date : le cas que
    la comparaison par taille et date ne sait pas voir."""
    source.write_text("version B", encoding="utf-8")
    destination.write_text("version A", encoding="utf-8")
    stamp = 1_700_000_000
    os.utime(source, (stamp, stamp))
    os.utime(destination, (stamp, stamp))


@pytestmark_rclone
def test_checksum_catches_a_change_that_size_and_date_miss(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    cloud = tmp_path / "cloud"
    remote_id = make_cloud(client, cloud)
    source = local_root / "Photos"
    source.mkdir()
    _same_size_same_date(source / "a.txt", cloud / "a.txt")
    task = make_task(client, remote_id, source)

    wait_for(client, start(client, task["id"], dry_run=False))
    assert (cloud / "a.txt").read_text(encoding="utf-8") == "version A"

    client.patch(f"/api/tasks/{task['id']}", json={"bandwidth": {"checksum": True}})
    run = wait_for(client, start(client, task["id"], dry_run=False))
    assert run["status"] == "success"
    assert (cloud / "a.txt").read_text(encoding="utf-8") == "version B"

    messages = [e["message"] for e in client.get(f"/api/runs/{run['id']}/events").json()]
    assert any("comparaison par somme de contrôle (" in m for m in messages)


@pytestmark_rclone
def test_without_a_common_checksum_the_option_is_not_applied(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Le piège : appliquer ``--checksum`` sans algorithme commun ferait
    comparer par taille seule. On revient à taille et date, et on le dit."""
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Photos"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")
    task = make_task(client, remote_id, source)
    client.patch(f"/api/tasks/{task['id']}", json={"bandwidth": {"checksum": True}})

    adapter = client.app.state.run_manager._adapter
    real_features = adapter.features
    launched: list[list[str]] = []
    real_start = adapter.start

    def features(fs: str, timeout: float = 30.0) -> dict:
        payload = real_features(fs, timeout=timeout)
        if ":" in fs:  # le côté distant ne déclare aucune somme de contrôle
            payload["Hashes"] = []
        return payload

    def spy(arguments: list[str]) -> subprocess.Popen[str]:
        launched.append(list(arguments))
        return real_start(arguments)

    adapter.features = features  # type: ignore[method-assign]
    adapter.start = spy  # type: ignore[method-assign]

    run = wait_for(client, start(client, task["id"], dry_run=False))
    assert run["status"] == "success"
    assert launched and all("--checksum" not in args for args in launched)

    events = client.get(f"/api/runs/{run['id']}/events").json()
    assert any(
        e["kind"] == "warning" and "aucune somme de contrôle commune" in e["message"]
        for e in events
    )


@pytestmark_rclone
def test_a_mirror_simulates_with_the_same_comparison_it_applies(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """§20.3 — mesurer les suppressions avec une comparaison différente de
    celle qui agira ne prouverait rien."""
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Photos"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")
    task = make_task(client, remote_id, source, mode="mirror")
    client.patch(f"/api/tasks/{task['id']}", json={"bandwidth": {"checksum": True}})
    wait_for(client, start(client, task["id"], dry_run=True))

    adapter = client.app.state.run_manager._adapter
    launched: list[list[str]] = []
    real_start = adapter.start

    def spy(arguments: list[str]) -> subprocess.Popen[str]:
        launched.append(list(arguments))
        return real_start(arguments)

    adapter.start = spy  # type: ignore[method-assign]
    run = wait_for(client, start(client, task["id"], dry_run=False))
    assert run["status"] == "success"
    assert len(launched) == 2  # simulation de contrôle, puis transfert
    assert all("--checksum" in args for args in launched)
