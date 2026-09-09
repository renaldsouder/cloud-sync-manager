"""Suite destructive du §20.3 — exécutée contre le vrai rclone.

Le §22 fixe le critère de réussite du produit : **zéro perte de données
silencieuse**. Ce fichier est l'endroit où cette promesse est vérifiée, et
aucune release contenant le Miroir ne doit passer sans lui.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_tasks_api import make_cloud, make_task, start, wait_for

pytestmark = pytest.mark.usefixtures("rclone_path")


def configure(client: TestClient, task_id: str, **fields: object) -> None:
    from csm.db.models import Task

    session = client.app.state.session_factory()
    try:
        task = session.get(Task, task_id)
        for key, value in fields.items():
            setattr(task, key, value)
        session.commit()
    finally:
        session.close()


def mirror_setup(
    client: TestClient,
    tmp_path: Path,
    local_root: Path,
    *,
    source_files: dict[str, str],
    cloud_files: dict[str, str],
) -> tuple[dict, Path, Path]:
    cloud = tmp_path / "cloud"
    cloud.mkdir(parents=True, exist_ok=True)
    source = local_root / "Photos"
    source.mkdir(parents=True, exist_ok=True)

    for name, content in source_files.items():
        (source / name).write_text(content, encoding="utf-8")
    for name, content in cloud_files.items():
        (cloud / name).write_text(content, encoding="utf-8")

    remote_id = make_cloud(client, cloud)
    task = make_task(client, remote_id, source, name="Miroir Photos", mode="mirror")
    return task, source, cloud


def surviving(cloud: Path) -> set[str]:
    """Fichiers encore en place, corbeille exclue."""
    return {
        entry.name
        for entry in cloud.iterdir()
        if entry.is_file() or entry.name != ".cloudsync-trash"
    } - {".cloudsync-trash"}


def quarantined(cloud: Path) -> set[str]:
    trash = cloud / ".cloudsync-trash"
    if not trash.is_dir():
        return set()
    return {entry.name for run in trash.iterdir() for entry in run.rglob("*") if entry.is_file()}


# -- §8.1 simulation obligatoire --------------------------------------------


def test_mirror_refuses_to_run_before_a_simulation(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    task, _, cloud = mirror_setup(
        client, tmp_path, local_root, source_files={"a.txt": "un"}, cloud_files={"vieux.txt": "x"}
    )

    refused = client.post(f"/api/tasks/{task['id']}/run", json={"dry_run": False})
    assert refused.status_code == 409
    assert "simulation" in refused.json()["detail"]
    assert (cloud / "vieux.txt").exists()

    wait_for(client, start(client, task["id"], dry_run=True))
    assert client.get("/api/tasks").json()[0]["dry_run_required"] is False
    assert (cloud / "vieux.txt").exists(), "la simulation a supprimé"


# -- §8.2 source inaccessible ou anormalement vide ---------------------------


def test_emptied_source_never_propagates_deletions(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Le scénario redouté : le share est démonté, la source paraît vide."""
    task, source, cloud = mirror_setup(
        client,
        tmp_path,
        local_root,
        source_files={"a.txt": "un", "b.txt": "deux"},
        cloud_files={"a.txt": "un", "b.txt": "deux"},
    )
    configure(client, task["id"], dry_run_required=False)

    for entry in source.iterdir():
        entry.unlink()

    run = wait_for(client, start(client, task["id"], dry_run=False))

    assert run["status"] == "blocked"
    assert run["deleted_files"] == 0
    assert surviving(cloud) == {"a.txt", "b.txt"}, "des fichiers ont été supprimés"
    assert client.get("/api/tasks").json()[0]["status"] == "blocked"


def test_unreachable_source_blocks(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    task, source, cloud = mirror_setup(
        client,
        tmp_path,
        local_root,
        source_files={"a.txt": "un"},
        cloud_files={"a.txt": "un", "b.txt": "deux"},
    )
    configure(client, task["id"], dry_run_required=False)
    shutil.rmtree(source)

    run = wait_for(client, start(client, task["id"], dry_run=False))

    assert run["status"] == "blocked"
    assert surviving(cloud) == {"a.txt", "b.txt"}


def test_confirmation_does_not_bypass_the_source_check(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Confirmer un seuil n'autorise pas à ignorer une source douteuse."""
    task, source, cloud = mirror_setup(
        client,
        tmp_path,
        local_root,
        source_files={"a.txt": "un"},
        cloud_files={"a.txt": "un", "b.txt": "deux"},
    )
    configure(client, task["id"], dry_run_required=False)
    for entry in source.iterdir():
        entry.unlink()

    response = client.post(
        f"/api/tasks/{task['id']}/run",
        json={"dry_run": False, "confirm_deletions": True},
    )
    run = wait_for(client, response.json()["id"])

    assert run["status"] == "blocked"
    assert surviving(cloud) == {"a.txt", "b.txt"}


# -- §8.3 seuil de suppression ----------------------------------------------


def test_one_deletion_passes_and_is_quarantined(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    task, _, cloud = mirror_setup(
        client,
        tmp_path,
        local_root,
        source_files={"a.txt": "un"},
        cloud_files={"a.txt": "un", "obsolete.txt": "à jeter"},
    )
    configure(client, task["id"], dry_run_required=False)

    run = wait_for(client, start(client, task["id"], dry_run=False))

    assert run["status"] == "success", run
    assert run["deleted_files"] == 1
    assert surviving(cloud) == {"a.txt"}
    # CONF-004 — la suppression est un déplacement, donc réversible.
    assert "obsolete.txt" in quarantined(cloud)


def test_ten_percent_is_at_the_limit_and_passes(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    kept = {f"f{i}.txt": "commun" for i in range(9)}
    task, _, cloud = mirror_setup(
        client,
        tmp_path,
        local_root,
        source_files=kept,
        cloud_files={**kept, "obsolete.txt": "à jeter"},
    )
    configure(
        client, task["id"], dry_run_required=False, max_deletes=100, max_delete_percent=10
    )

    run = wait_for(client, start(client, task["id"], dry_run=False))

    assert run["status"] == "success", run
    assert run["deleted_files"] == 1
    assert len(surviving(cloud)) == 9


def test_hundred_percent_is_blocked_and_lists_the_files(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """§10.4 — l'utilisateur doit voir *quoi*, pas seulement *combien*."""
    doomed = {f"photo{i}.jpg": "contenu" for i in range(12)}
    task, _, cloud = mirror_setup(
        client,
        tmp_path,
        local_root,
        source_files={"seul.txt": "je reste"},
        cloud_files=doomed,
    )
    configure(client, task["id"], dry_run_required=False)

    run = wait_for(client, start(client, task["id"], dry_run=False))

    assert run["status"] == "blocked"
    assert run["deleted_files"] == 0
    assert surviving(cloud) == set(doomed)
    assert quarantined(cloud) == set()

    planned = client.get(
        f"/api/runs/{run['id']}/events", params={"kind": "skip_delete"}
    ).json()
    assert {event["path"] for event in planned} == set(doomed)

    summary = run["summary"]["deletion_plan"]
    assert summary["deletes"] == 12
    assert summary["percent"] == 100.0


def test_custom_threshold_blocks(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    cloud_files = {"a.txt": "un", **{f"vieux{i}.txt": "x" for i in range(9)}}
    task, _, cloud = mirror_setup(
        client,
        tmp_path,
        local_root,
        source_files={"a.txt": "un"},
        cloud_files=cloud_files,
    )
    configure(
        client, task["id"], dry_run_required=False, max_deletes=2, max_delete_percent=100
    )

    run = wait_for(client, start(client, task["id"], dry_run=False))

    assert run["status"] == "blocked"
    assert "seuil de 2" in run["summary"]["blocked_reason"]
    assert surviving(cloud) == set(cloud_files)


def test_explicit_confirmation_unblocks(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    doomed = {f"photo{i}.jpg": "contenu" for i in range(12)}
    task, _, cloud = mirror_setup(
        client,
        tmp_path,
        local_root,
        source_files={"seul.txt": "je reste"},
        cloud_files=doomed,
    )
    configure(client, task["id"], dry_run_required=False)

    blocked = wait_for(client, start(client, task["id"], dry_run=False))
    assert blocked["status"] == "blocked"

    response = client.post(
        f"/api/tasks/{task['id']}/run",
        json={"dry_run": False, "confirm_deletions": True},
    )
    assert response.status_code == 202
    run = wait_for(client, response.json()["id"])

    assert run["status"] == "success", run
    assert run["deleted_files"] == 12
    assert surviving(cloud) == {"seul.txt"}
    assert quarantined(cloud) == set(doomed), "les fichiers doivent rester récupérables"


# -- §20.3 cohérence simulation / exécution ---------------------------------


def test_simulation_matches_the_real_run(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    cloud_files = {"a.txt": "un", "vieux1.txt": "x", "vieux2.txt": "y"}
    task, _, cloud = mirror_setup(
        client,
        tmp_path,
        local_root,
        source_files={"a.txt": "un"},
        cloud_files=cloud_files,
    )

    simulated = wait_for(client, start(client, task["id"], dry_run=True))
    planned = client.get(
        f"/api/runs/{simulated['id']}/events", params={"kind": "skip_delete"}
    ).json()
    planned_paths = {event["path"] for event in planned}
    assert planned_paths == {"vieux1.txt", "vieux2.txt"}
    assert surviving(cloud) == set(cloud_files), "la simulation a modifié le disque"

    real = wait_for(client, start(client, task["id"], dry_run=False))
    assert real["status"] == "success", real
    assert real["deleted_files"] == len(planned_paths)
    assert surviving(cloud) == {"a.txt"}


def test_replaced_versions_are_recoverable(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """CONF-004 — l'écrasement est aussi une perte, la corbeille le couvre."""
    # Les deux versions doivent différer par la taille : à taille et date
    # identiques, rclone considère à juste titre le fichier inchangé.
    task, source, cloud = mirror_setup(
        client,
        tmp_path,
        local_root,
        source_files={"note.txt": "version 2, nettement plus longue qu'avant"},
        cloud_files={"note.txt": "v1"},
    )
    configure(client, task["id"], dry_run_required=False)

    run = wait_for(client, start(client, task["id"], dry_run=False))

    assert run["status"] == "success", run
    assert (cloud / "note.txt").read_text(encoding="utf-8").startswith("version 2")

    trash = cloud / ".cloudsync-trash"
    saved = [path for path in trash.rglob("note.txt")]
    assert saved, "l'ancienne version n'a pas été conservée"
    assert saved[0].read_text(encoding="utf-8") == "v1"


def test_second_run_is_not_tripped_by_its_own_quarantine(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """La corbeille ne doit pas être comptée comme « à supprimer » ensuite."""
    task, _, cloud = mirror_setup(
        client,
        tmp_path,
        local_root,
        source_files={"a.txt": "un"},
        cloud_files={"a.txt": "un", **{f"vieux{i}.txt": "x" for i in range(5)}},
    )
    configure(client, task["id"], dry_run_required=False, max_deletes=6)

    first = wait_for(client, start(client, task["id"], dry_run=False))
    assert first["status"] == "success", first
    assert len(quarantined(cloud)) == 5

    second = wait_for(client, start(client, task["id"], dry_run=False))
    assert second["status"] == "success", second
    assert second["deleted_files"] == 0
    assert len(quarantined(cloud)) == 5, "la corbeille a été touchée"
