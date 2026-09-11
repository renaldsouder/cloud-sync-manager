"""Ligne de commande du bidirectionnel (SYNC-003, §7.3, §8.6).

Ces tests couvrent la construction des arguments — fonction pure — et
vérifient contre le vrai rclone les comportements sur lesquels repose toute
la conception. Ils ont été écrits après mesure, pas d'après la documentation :
plusieurs suppositions raisonnables se sont révélées fausses.

Le mode reste indisponible à l'exécution tant que la matrice du §20.3 n'est
pas complète (§21).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from csm.rclone.adapter import (
    BISYNC_CHECK_FILENAME,
    BISYNC_NEEDS_RESYNC,
    RcloneAdapter,
)

ANSI = re.compile(r"\x1b?\[[0-9;]*m")


@pytest.fixture
def adapter(rclone_path: str, tmp_path: Path) -> RcloneAdapter:
    return RcloneAdapter(binary=rclone_path, config_path=tmp_path / "rclone.conf")


# -- construction des arguments ----------------------------------------------


def test_the_command_is_bisync(adapter: RcloneAdapter, tmp_path: Path) -> None:
    args = adapter.build_bisync_args("un:", "deux:", str(tmp_path / "work"))
    assert args[0] == "bisync"
    assert args[1:3] == ["un:", "deux:"]


def test_a_working_directory_is_mandatory(adapter: RcloneAdapter) -> None:
    """Sans lui, bisync range ses listings dans un cache qui meurt avec le
    conteneur — et chaque recréation deviendrait une ré-initialisation."""
    with pytest.raises(ValueError, match="répertoire de travail"):
        adapter.build_bisync_args("un:", "deux:", "")


def test_colour_is_disabled(adapter: RcloneAdapter, tmp_path: Path) -> None:
    """bisync colore sa sortie même redirigée, jusque dans le journal JSON."""
    args = adapter.build_bisync_args("un:", "deux:", str(tmp_path / "work"))
    assert args[args.index("--color") + 1] == "NEVER"


def test_the_simulation_differs_only_by_dry_run(
    adapter: RcloneAdapter, tmp_path: Path
) -> None:
    """§20.3 — une simulation qui ne correspond pas à l'exécution ne prouve rien."""
    work = str(tmp_path / "work")
    real = adapter.build_bisync_args("un:", "deux:", work)
    simulated = adapter.build_bisync_args("un:", "deux:", work, dry_run=True)
    assert [a for a in simulated if a != "--dry-run"] == real


def test_resync_is_never_implicit(adapter: RcloneAdapter, tmp_path: Path) -> None:
    """§7.3 — jamais d'initialisation sans action explicite de l'utilisateur."""
    work = str(tmp_path / "work")
    assert "--resync" not in adapter.build_bisync_args("un:", "deux:", work)
    assert "--resync" in adapter.build_bisync_args("un:", "deux:", work, resync=True)


def test_a_simulation_of_the_initialisation_is_possible(
    adapter: RcloneAdapter, tmp_path: Path
) -> None:
    """§8.1 — un essai à blanc est exigé avant l'initialisation bidirectionnelle."""
    args = adapter.build_bisync_args(
        "un:", "deux:", str(tmp_path / "work"), resync=True, dry_run=True
    )
    assert "--resync" in args and "--dry-run" in args


def test_access_check_names_its_marker(adapter: RcloneAdapter, tmp_path: Path) -> None:
    """§8.6 — vérification d'accès aux deux côtés."""
    args = adapter.build_bisync_args(
        "un:", "deux:", str(tmp_path / "work"), check_access=True
    )
    assert "--check-access" in args
    assert args[args.index("--check-filename") + 1] == BISYNC_CHECK_FILENAME


def test_the_backup_directories_are_per_side(
    adapter: RcloneAdapter, tmp_path: Path
) -> None:
    """bisync n'accepte pas --backup-dir : une destination par côté."""
    args = adapter.build_bisync_args(
        "un:",
        "deux:",
        str(tmp_path / "work"),
        backup_dir1="un:/.cloudsync-trash",
        backup_dir2="deux:/.cloudsync-trash",
    )
    assert "--backup-dir" not in args
    assert args[args.index("--backup-dir1") + 1] == "un:/.cloudsync-trash"
    assert args[args.index("--backup-dir2") + 1] == "deux:/.cloudsync-trash"


def test_the_filter_file_uses_the_bisync_spelling(
    adapter: RcloneAdapter, tmp_path: Path
) -> None:
    args = adapter.build_bisync_args(
        "un:", "deux:", str(tmp_path / "work"), filter_file="/config/f.filter"
    )
    assert "--filter-from" not in args
    assert args[args.index("--filters-file") + 1] == "/config/f.filter"


@pytest.mark.parametrize(
    ("kwargs", "motif"),
    [
        ({"conflict_resolve": "inconnu"}, "arbitrage"),
        ({"conflict_loser": "inconnu"}, "perdant"),
    ],
)
def test_an_unknown_conflict_policy_is_refused(
    adapter: RcloneAdapter, tmp_path: Path, kwargs: dict, motif: str
) -> None:
    with pytest.raises(ValueError, match=motif):
        adapter.build_bisync_args("un:", "deux:", str(tmp_path / "work"), **kwargs)


def test_nothing_is_destroyed_by_default(adapter: RcloneAdapter, tmp_path: Path) -> None:
    """Le perdant d'un conflit est renommé, jamais supprimé, sauf demande."""
    args = adapter.build_bisync_args("un:", "deux:", str(tmp_path / "work"))
    assert args[args.index("--conflict-resolve") + 1] == "none"
    assert args[args.index("--conflict-loser") + 1] == "num"


def test_secrets_never_reach_the_command_line(
    adapter: RcloneAdapter, tmp_path: Path
) -> None:
    """SEC-001 — les identifiants vivent dans rclone.conf, pas dans argv."""
    args = adapter.build_bisync_args("onedrive:", "/mnt/user/photos", str(tmp_path))
    assert not any("token" in a or "password" in a for a in args)


# -- comportements mesurés contre le vrai rclone -----------------------------


def bisync(adapter: RcloneAdapter, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [adapter.binary, "--config", str(adapter.config_path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


@pytest.fixture
def paires(tmp_path: Path) -> tuple[str, str, str]:
    p1, p2, work = tmp_path / "p1", tmp_path / "p2", tmp_path / "work"
    for dossier in (p1, p2, work):
        dossier.mkdir()
    return str(p1), str(p2), str(work)


def test_a_first_run_without_initialisation_is_refused(
    adapter: RcloneAdapter, paires: tuple[str, str, str]
) -> None:
    """Le comportement de repli est l'arrêt, pas une propagation hasardeuse.

    Le code 7 est la façon dont bisync réclame une ré-initialisation. Le
    traiter comme une panne ordinaire cacherait la seule action utile.
    """
    p1, p2, work = paires
    Path(p1, "a.txt").write_text("a", encoding="utf-8")

    result = bisync(adapter, *adapter.build_bisync_args(p1, p2, work))
    assert result.returncode == BISYNC_NEEDS_RESYNC
    assert "resync" in result.stderr.lower()
    assert list(Path(p2).iterdir()) == [], "rien ne doit avoir été propagé"


def test_the_initialisation_merges_both_sides(
    adapter: RcloneAdapter, paires: tuple[str, str, str]
) -> None:
    p1, p2, work = paires
    Path(p1, "a.txt").write_text("a", encoding="utf-8")
    Path(p2, "b.txt").write_text("b", encoding="utf-8")

    result = bisync(adapter, *adapter.build_bisync_args(p1, p2, work, resync=True))
    assert result.returncode == 0, result.stderr[-800:]
    assert {f.name for f in Path(p1).iterdir()} == {"a.txt", "b.txt"}
    assert {f.name for f in Path(p2).iterdir()} == {"a.txt", "b.txt"}


def test_the_simulation_writes_nothing(
    adapter: RcloneAdapter, paires: tuple[str, str, str]
) -> None:
    p1, p2, work = paires
    Path(p1, "a.txt").write_text("a", encoding="utf-8")
    bisync(adapter, *adapter.build_bisync_args(p1, p2, work, resync=True))

    Path(p1, "neuf.txt").write_text("neuf", encoding="utf-8")
    result = bisync(adapter, *adapter.build_bisync_args(p1, p2, work, dry_run=True))
    assert result.returncode == 0, result.stderr[-800:]
    assert not Path(p2, "neuf.txt").exists()


def test_lost_listings_demand_a_new_initialisation(
    adapter: RcloneAdapter, paires: tuple[str, str, str]
) -> None:
    """C'est pourquoi le répertoire de travail doit survivre au conteneur."""
    p1, p2, work = paires
    Path(p1, "a.txt").write_text("a", encoding="utf-8")
    bisync(adapter, *adapter.build_bisync_args(p1, p2, work, resync=True))

    for listing in Path(work).glob("*.lst"):
        listing.unlink()

    result = bisync(adapter, *adapter.build_bisync_args(p1, p2, work))
    assert result.returncode == BISYNC_NEEDS_RESYNC


def test_the_json_log_carries_no_escape_sequences(
    adapter: RcloneAdapter, paires: tuple[str, str, str]
) -> None:
    """Sans --color NEVER, des codes ANSI finiraient dans l'historique."""
    p1, p2, work = paires
    Path(p1, "a.txt").write_text("a", encoding="utf-8")

    result = bisync(adapter, *adapter.build_bisync_args(p1, p2, work, resync=True))
    lignes = [ligne for ligne in result.stderr.splitlines() if ligne.strip()]
    assert lignes, "bisync doit journaliser sur stderr"
    for ligne in lignes:
        message = json.loads(ligne).get("msg", "")
        assert not ANSI.search(message), f"séquence ANSI dans : {message[:80]}"
