"""Rétention de la quarantaine (§16, CONF-004).

Sans purge, un Miroir actif finirait par remplir la destination avec ses
propres sauvegardes : une protection qui provoque une panne de stockage
cesse d'en être une.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.services.guards import (
    expired_batches,
    is_quarantine_path,
    parse_stamp,
    quarantine_directory,
    quarantine_root,
)
from tests.test_mirror_guards import configure, mirror_setup, quarantined
from tests.test_tasks_api import start, wait_for

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def stamp(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y%m%d-%H%M%S")


# -- règle de rétention -----------------------------------------------------


def test_recent_batches_are_kept() -> None:
    names = [stamp(1), stamp(5), stamp(29)]
    assert expired_batches(names, now=NOW, retention_days=30, keep_last=3) == []


def test_old_batches_are_purged() -> None:
    old, older = stamp(40), stamp(60)
    names = [stamp(1), stamp(2), stamp(3), old, older]
    assert set(expired_batches(names, now=NOW, retention_days=30, keep_last=3)) == {
        old,
        older,
    }


def test_the_last_batches_survive_the_retention() -> None:
    """Après une erreur restée longtemps inaperçue, il doit rester de quoi
    récupérer, même si toutes les corbeilles ont dépassé la rétention."""
    names = [stamp(100), stamp(200), stamp(300), stamp(400)]
    survivors = set(names) - set(
        expired_batches(names, now=NOW, retention_days=30, keep_last=3)
    )
    assert survivors == {stamp(100), stamp(200), stamp(300)}


def test_foreign_directories_are_never_touched() -> None:
    """La purge ne doit jamais supprimer un dossier qu'elle n'a pas créé."""
    names = [stamp(400), "photos-de-vacances", "20260909", "sauvegarde-importante", ""]
    assert expired_batches(names, now=NOW, retention_days=30, keep_last=0) == [
        stamp(400)
    ]


def test_stamp_parsing_is_strict() -> None:
    assert parse_stamp("20260909-120000") == datetime(
        2026, 9, 9, 12, 0, tzinfo=timezone.utc
    )
    assert parse_stamp("20261399-120000") is None  # mois inexistant
    assert parse_stamp("mes-photos") is None


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/mnt/user/Photos/.cloudsync-trash/20260909-120000", True),
        ("gdrive:Sauvegardes/.cloudsync-trash/20260909-120000", True),
        ("/mnt/user/Photos/.cloudsync-trash", False),
        ("/mnt/user/Photos", False),
        ("/mnt/user/Photos/.cloudsync-trash/important", False),
        ("/mnt/user", False),
    ],
)
def test_only_our_own_trash_paths_are_purgeable(path: str, expected: bool) -> None:
    assert is_quarantine_path(path) is expected


def test_quarantine_paths_are_well_formed() -> None:
    assert quarantine_root("gdrive:") == "gdrive:.cloudsync-trash"
    assert quarantine_root("gdrive:Photos") == "gdrive:Photos/.cloudsync-trash"
    assert quarantine_root("/mnt/user/Photos") == "/mnt/user/Photos/.cloudsync-trash"
    assert is_quarantine_path(quarantine_directory("gdrive:"))


# -- purge réelle -----------------------------------------------------------


@pytest.mark.usefixtures("rclone_path")
def test_old_quarantines_are_purged_after_a_run(
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

    trash = cloud / ".cloudsync-trash"
    # Cinq corbeilles antérieures, dont quatre très anciennes, plus un
    # dossier qui n'est pas de nous.
    old = [stamp(days) for days in (340, 360, 380, 400)]
    for name in [*old, stamp(2), "à-ne-pas-toucher"]:
        folder = trash / name
        folder.mkdir(parents=True)
        (folder / "contenu.txt").write_text("x", encoding="utf-8")

    run = wait_for(client, start(client, task["id"], dry_run=False))
    assert run["status"] == "success", run

    remaining = {entry.name for entry in trash.iterdir()}

    # Périmées et hors des trois plus récentes : purgées.
    assert not ({stamp(360), stamp(380), stamp(400)} & remaining)
    # Périmée mais parmi les trois dernières conservées : elle survit, c'est
    # ce qui laisse de quoi récupérer après une erreur restée inaperçue.
    assert stamp(340) in remaining
    assert stamp(2) in remaining
    assert "à-ne-pas-toucher" in remaining, "un dossier étranger a été supprimé"
    # La corbeille de cette exécution est bien là, avec le fichier retiré.
    assert "obsolete.txt" in quarantined(cloud)
