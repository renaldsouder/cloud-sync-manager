"""LOCAL-005 et P13 — confinement des chemins et destinations protégées."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from csm.paths import (
    PathNotAllowed,
    assert_valid_destination,
    paths_overlap,
    resolve_within_roots,
    to_remote_path,
)


def test_accepts_path_inside_root(local_root: Path) -> None:
    target = local_root / "Photos" / "2026"
    target.mkdir(parents=True)
    assert resolve_within_roots(target, (local_root,)) == Path(os.path.realpath(target))


def test_rejects_parent_traversal(local_root: Path) -> None:
    with pytest.raises(PathNotAllowed):
        resolve_within_roots(local_root / ".." / ".." / "etc", (local_root,))


def test_rejects_relative_path(local_root: Path) -> None:
    with pytest.raises(PathNotAllowed):
        resolve_within_roots("Photos/2026", (local_root,))


def test_rejects_sibling_root(local_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "ailleurs"
    outside.mkdir()
    with pytest.raises(PathNotAllowed):
        resolve_within_roots(outside, (local_root,))


def test_rejects_symlink_escaping_root(local_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "secret"
    outside.mkdir()
    link = local_root / "evasion"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("création de lien symbolique non autorisée sur cet hôte")
    with pytest.raises(PathNotAllowed):
        resolve_within_roots(link, (local_root,))


def test_root_itself_is_not_a_valid_destination(local_root: Path) -> None:
    resolved = resolve_within_roots(local_root, (local_root,))
    with pytest.raises(PathNotAllowed):
        assert_valid_destination(resolved, (local_root,))


@pytest.mark.parametrize("share", ["appdata", "system", "domains", "AppData"])
def test_protected_shares_are_refused_as_destination(local_root: Path, share: str) -> None:
    target = local_root / share / "sous-dossier"
    target.mkdir(parents=True)
    resolved = resolve_within_roots(target, (local_root,))
    with pytest.raises(PathNotAllowed):
        assert_valid_destination(resolved, (local_root,))


def test_ordinary_share_is_a_valid_destination(local_root: Path) -> None:
    target = local_root / "Photos"
    target.mkdir()
    resolved = resolve_within_roots(target, (local_root,))
    assert_valid_destination(resolved, (local_root,))


def test_overlap_detection(local_root: Path) -> None:
    parent = local_root / "Documents"
    child = parent / "Factures"
    other = local_root / "Photos"
    assert paths_overlap(parent, child)
    assert paths_overlap(child, parent)
    assert paths_overlap(parent, parent)
    assert not paths_overlap(parent, other)


def test_remote_path_normalisation() -> None:
    assert to_remote_path("/Photos/2026/") == "Photos/2026"
    assert to_remote_path("Photos\\2026") == "Photos/2026"
    with pytest.raises(PathNotAllowed):
        to_remote_path("Photos/../../etc")
