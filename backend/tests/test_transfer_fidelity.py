"""Fidélité des transferts, contre le vrai rclone (SYNC-007, GLOBAL-005,
PERF-004, PERF-005).

Chaque scénario passe par l'API, comme l'utilisateur : création de la
tâche, exécution, relevé. Le stockage distant est un dossier local derrière
un alias rclone — le seul fournisseur qu'on puisse éprouver ici, et qui
conserve dates, noms et contenus à l'identique : tout écart est donc le
nôtre.
"""

from __future__ import annotations

import hashlib
import os
import time
import unicodedata
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.rclone.adapter import RcloneAdapter
from csm.services.guards import QUARANTINE_DIR_NAME
from tests.test_mirror_guards import configure
from tests.test_tasks_api import (
    _throttle,
    _wait_until_started,
    make_cloud,
    make_task,
    start,
    wait_for,
)

pytestmark = pytest.mark.usefixtures("rclone_path")

#: 2 janvier 2020, 03:04:05 UTC — loin de « maintenant », pour qu'une date
#: remplacée par l'heure de la copie se voie.
OLD = 1577934245


def run(client: TestClient, task_id: str, *, dry_run: bool = False) -> dict:
    return wait_for(client, start(client, task_id, dry_run=dry_run))


def tree(root: Path) -> dict[str, bytes]:
    """Contenu d'une arborescence, chemins relatifs normalisés en ``/``."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and QUARANTINE_DIR_NAME not in path.parts
    }


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


# == SYNC-007 — dates de modification =========================================


def test_a_copy_to_the_cloud_keeps_the_modification_date(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    source = local_root / "Photos"
    source.mkdir()
    photo = source / "photo.jpg"
    photo.write_bytes(b"jpeg")
    os.utime(photo, (OLD, OLD))
    cloud = tmp_path / "cloud"

    task = make_task(client, make_cloud(client, cloud), source)
    assert run(client, task["id"])["status"] == "success"

    assert abs((cloud / "photo.jpg").stat().st_mtime - OLD) < 1


def test_a_copy_from_the_cloud_keeps_the_modification_date(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    cloud = tmp_path / "cloud"
    cloud.mkdir()
    document = cloud / "facture.pdf"
    document.write_bytes(b"pdf")
    os.utime(document, (OLD, OLD))
    destination = local_root / "Factures"
    destination.mkdir()

    task = make_task(
        client, make_cloud(client, cloud), destination, direction="remote_to_local"
    )
    assert run(client, task["id"])["status"] == "success"

    assert abs((destination / "facture.pdf").stat().st_mtime - OLD) < 1


def test_an_unchanged_file_is_not_sent_again(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """La date conservée est ce qui permet au passage suivant de ne rien
    renvoyer : sans elle, chaque exécution recopierait tout."""
    source = local_root / "Photos"
    source.mkdir()
    for index in range(5):
        (source / f"{index}.txt").write_text(str(index), encoding="utf-8")
    task = make_task(client, make_cloud(client, tmp_path / "cloud"), source)

    assert run(client, task["id"])["transferred_files"] == 5
    second = run(client, task["id"])
    assert second["status"] == "success"
    assert second["transferred_files"] == 0


def test_a_date_only_change_is_propagated_without_resending(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    source = local_root / "Photos"
    source.mkdir()
    photo = source / "photo.jpg"
    photo.write_bytes(b"jpeg")
    os.utime(photo, (OLD, OLD))
    cloud = tmp_path / "cloud"
    task = make_task(client, make_cloud(client, cloud), source)
    run(client, task["id"])

    newer = OLD + 86400
    os.utime(photo, (newer, newer))
    assert run(client, task["id"])["status"] == "success"

    assert abs((cloud / "photo.jpg").stat().st_mtime - newer) < 1
    assert (cloud / "photo.jpg").read_bytes() == b"jpeg"


@pytest.mark.parametrize("operation", ["copy", "sync"])
@pytest.mark.parametrize("checksum", [False, True])
def test_no_option_ever_discards_the_dates(
    tmp_path: Path, operation: str, checksum: bool
) -> None:
    """Aucun réglage que l'application expose ne doit conduire rclone à
    ignorer ou à ne pas écrire les dates."""
    adapter = RcloneAdapter(binary="rclone", config_path=tmp_path / "rclone.conf")
    arguments = adapter.build_transfer_args(
        operation,
        "/src",
        "dst:x",
        checksum=checksum,
        transfers=8,
        checkers=16,
        bwlimit="08:00,512k 23:00,off",
        backup_dir=f"dst:{QUARANTINE_DIR_NAME}/x",
    )
    for forbidden in ("--no-update-modtime", "--ignore-times", "--size-only", "--metadata-exclude"):
        assert forbidden not in arguments


# == GLOBAL-005 — noms Unicode, espaces, caractères spéciaux, profondeur ======

DIFFICULT_NAMES = [
    "Été 2026/ça & là (1)/fichier #1 [final] {v2}.txt",
    "Été 2026/l'apostrophe et \"guillemets\".txt",
    "emoji 📷 🎉.jpg",
    "日本語/写真.png",
    "Ελληνικά/κείμενο.txt",
    "symboles/pourcent %20 dollar $HOME.txt",
    "symboles/point-virgule;virgule,égal=plus+.txt",
    "symboles/-rf",
    "symboles/--dry-run",
    "espaces/deux  espaces.txt",
    "espaces/ début par une espace.txt",
]


def write_tree(root: Path, names: list[str]) -> None:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")


def test_difficult_names_are_copied_exactly(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Un nom qui ressemble à une option (``--dry-run``, ``-rf``) ne doit
    jamais être lu comme telle : chemins et options ne se mélangent pas."""
    source = local_root / "Données"
    write_tree(source, DIFFICULT_NAMES)
    cloud = tmp_path / "cloud"

    task = make_task(client, make_cloud(client, cloud), source)
    result = run(client, task["id"])

    assert result["status"] == "success", result
    assert result["transferred_files"] == len(DIFFICULT_NAMES)
    assert tree(cloud) == tree(source)


def test_difficult_names_survive_a_mirror_round(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Deuxième passage d'un Miroir sur ces noms : rien à transférer, rien à
    supprimer. Un nom mal relu d'un côté apparaîtrait comme un fichier à
    supprimer et à recopier à chaque exécution."""
    source = local_root / "Données"
    write_tree(source, DIFFICULT_NAMES)
    cloud = tmp_path / "cloud"
    task = make_task(client, make_cloud(client, cloud), source, mode="mirror")

    assert run(client, task["id"], dry_run=True)["status"] == "success"
    assert run(client, task["id"])["status"] == "success"
    again = run(client, task["id"])

    assert again["status"] == "success"
    assert again["transferred_files"] == 0
    assert again["deleted_files"] == 0
    assert tree(cloud) == tree(source)


def test_deleting_a_difficult_name_goes_to_the_trash_intact(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    source = local_root / "Données"
    write_tree(source, DIFFICULT_NAMES)
    cloud = tmp_path / "cloud"
    task = make_task(client, make_cloud(client, cloud), source, mode="mirror")
    configure(client, task["id"], dry_run_required=False, max_delete_percent=100)
    run(client, task["id"])

    victim = "Été 2026/ça & là (1)/fichier #1 [final] {v2}.txt"
    (source / victim).unlink()
    result = run(client, task["id"])

    assert result["status"] == "success"
    assert result["deleted_files"] == 1
    assert not (cloud / victim).exists()
    trashed = [p for p in (cloud / QUARANTINE_DIR_NAME).rglob("*") if p.is_file()]
    assert [p.name for p in trashed] == ["fichier #1 [final] {v2}.txt"]
    assert trashed[0].read_text(encoding="utf-8") == victim


def test_decomposed_accents_do_not_loop(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Un nom écrit depuis un Mac arrive souvent décomposé (NFD : « e » suivi
    d'un accent combinant). Le Miroir ne doit pas le prendre, au passage
    suivant, pour un autre fichier à supprimer."""
    source = local_root / "Mac"
    source.mkdir()
    decomposed = unicodedata.normalize("NFD", "été.txt")
    assert decomposed != "été.txt"
    (source / decomposed).write_text("mac", encoding="utf-8")
    cloud = tmp_path / "cloud"
    task = make_task(client, make_cloud(client, cloud), source, mode="mirror")
    configure(client, task["id"], dry_run_required=False)

    run(client, task["id"])
    again = run(client, task["id"])

    assert again["transferred_files"] == 0
    assert again["deleted_files"] == 0
    assert len([p for p in cloud.iterdir() if p.is_file()]) == 1


def test_a_deep_tree_is_copied(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Soixante niveaux, plus de mille caractères de chemin."""
    source = local_root / "Profond"
    deep = source
    for level in range(60):
        deep = deep / f"niveau {level:02d} — dossier"
    deep.mkdir(parents=True)
    (deep / "tout au fond.txt").write_text("fond", encoding="utf-8")
    assert len(str(deep)) > 1000
    cloud = tmp_path / "cloud"

    task = make_task(client, make_cloud(client, cloud), source)
    assert run(client, task["id"])["status"] == "success"

    assert tree(cloud) == tree(source)


def test_task_paths_themselves_may_be_difficult(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    source = local_root / "Mes Données" / "Été & co (2026)"
    source.mkdir(parents=True)
    (source / "a.txt").write_text("a", encoding="utf-8")
    cloud = tmp_path / "cloud"

    task = make_task(
        client,
        make_cloud(client, cloud),
        source,
        remote_path="Sauvegarde été/2026 [1]",
    )
    assert run(client, task["id"])["status"] == "success"

    assert (cloud / "Sauvegarde été" / "2026 [1]" / "a.txt").read_text() == "a"


# == PERF-004 — beaucoup de petits fichiers, un gros fichier ==================

MANY = 3000


def many_small_files(root: Path, count: int = MANY) -> None:
    for index in range(count):
        folder = root / f"lot {index // 100:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"petit {index:05d}.txt").write_text(str(index), encoding="utf-8")


def test_many_small_files_are_all_copied_and_counted(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    source = local_root / "Petits"
    many_small_files(source)
    cloud = tmp_path / "cloud"
    task = make_task(client, make_cloud(client, cloud), source)

    first = run(client, task["id"])
    assert first["status"] == "success"
    assert first["transferred_files"] == MANY
    assert sum(1 for p in cloud.rglob("*") if p.is_file()) == MANY

    second = run(client, task["id"])
    assert second["transferred_files"] == 0


def test_a_mass_deletion_among_many_files_is_blocked(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Deux mille fichiers disparus de la source : le seuil arrête le Miroir
    avant toute suppression, quel que soit le nombre de fichiers."""
    source = local_root / "Petits"
    many_small_files(source)
    cloud = tmp_path / "cloud"
    task = make_task(client, make_cloud(client, cloud), source, mode="mirror")
    configure(client, task["id"], dry_run_required=False)
    assert run(client, task["id"])["status"] == "success"

    for folder in sorted(source.iterdir())[:20]:
        for path in folder.iterdir():
            path.unlink()
    result = run(client, task["id"])

    assert result["status"] == "blocked"
    assert result["deleted_files"] == 0
    assert sum(1 for p in cloud.rglob("*") if p.is_file()) == MANY


def test_a_large_file_is_copied_intact(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    source = local_root / "Gros"
    source.mkdir()
    big = source / "image disque.img"
    with big.open("wb") as handle:
        for _ in range(64):
            handle.write(os.urandom(1 << 20))
    cloud = tmp_path / "cloud"

    task = make_task(client, make_cloud(client, cloud), source)
    configure(client, task["id"], bandwidth_json='{"checksum": true}')
    result = run(client, task["id"])

    assert result["status"] == "success"
    assert result["transferred_bytes"] == 64 << 20
    assert digest(cloud / "image disque.img") == digest(big)


# == PERF-005 — reprise et nettoyage des transferts interrompus ===============


def leftovers(root: Path) -> list[str]:
    return [p.name for p in root.rglob("*") if p.is_file() and p.name.endswith(".partial")]


def test_an_interrupted_transfer_leaves_no_partial_file(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Un arrêt en plein fichier ne doit laisser ni fichier tronqué sous le
    nom définitif, ni morceau « .partial » à la destination."""
    source = local_root / "Gros"
    source.mkdir()
    big = source / "gros.bin"
    big.write_bytes(os.urandom(4 << 20))
    cloud = tmp_path / "cloud"
    task = make_task(client, make_cloud(client, cloud), source)
    _throttle(client, task["id"], "100k")

    run_id = start(client, task["id"], dry_run=False)
    _wait_until_started(client, run_id)
    time.sleep(1.0)
    assert client.post(f"/api/runs/{run_id}/stop").status_code == 200
    stopped = wait_for(client, run_id, timeout=60)

    assert stopped["status"] == "interrupted"
    assert leftovers(cloud) == []
    final = cloud / "gros.bin"
    assert not final.exists() or digest(final) == digest(big)


def test_a_rerun_after_an_interruption_resumes_where_it_stopped(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """La reprise se fait fichier par fichier : ce qui est arrivé entier
    avant l'arrêt n'est pas renvoyé, le reste l'est, et rien n'est perdu."""
    source = local_root / "Lot"
    source.mkdir()
    for index in range(40):
        (source / f"{index:02d}.bin").write_bytes(os.urandom(32 << 10))
    cloud = tmp_path / "cloud"
    task = make_task(client, make_cloud(client, cloud), source)
    configure(client, task["id"], bandwidth_json='{"limit": "200k", "transfers": 1}')

    run_id = start(client, task["id"], dry_run=False)
    _wait_until_started(client, run_id)
    time.sleep(2.0)
    client.post(f"/api/runs/{run_id}/stop")
    first = wait_for(client, run_id, timeout=60)
    assert first["status"] == "interrupted"
    arrived = sum(1 for p in cloud.iterdir() if p.is_file()) if cloud.exists() else 0
    assert 0 < arrived < 40, "l'arrêt doit tomber au milieu du lot"

    configure(client, task["id"], bandwidth_json=None)
    second = run(client, task["id"])

    assert second["status"] == "success"
    assert second["transferred_files"] <= 40 - arrived + 1
    assert leftovers(cloud) == []
    assert tree(cloud) == tree(source)
