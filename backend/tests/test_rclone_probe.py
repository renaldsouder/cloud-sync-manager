"""Sonde rclone — exécutée contre le **vrai** binaire.

Le §19 exige un test d'intégration dès qu'un comportement touche rclone.
C'est exactement ce que ce fichier aurait attrapé au J1 : la sonde utilisait
``rclone version --json``, un drapeau qui n'existe pas, et aurait signalé
rclone comme indisponible dans le conteneur.
"""

from __future__ import annotations

import sys
from pathlib import Path

from csm.rclone.probe import rclone_binary, rclone_version


def test_version_from_real_binary(rclone_path: str) -> None:
    info = rclone_version(rclone_path)
    assert info is not None, "le binaire réel doit répondre"
    assert info.version.startswith("v")
    assert info.os
    assert info.arch
    assert info.go_version


def test_absent_binary_returns_none(tmp_path: Path) -> None:
    assert rclone_version(str(tmp_path / "rclone-inexistant")) is None


def test_binary_that_is_not_rclone_returns_none() -> None:
    """Un exécutable qui n'est pas rclone ne doit pas faire tomber la sonde."""
    assert rclone_version(sys.executable) is None


def test_configured_path_wins_over_path_lookup(rclone_path: str) -> None:
    assert rclone_binary(rclone_path) == rclone_path


def test_configured_path_must_exist(tmp_path: Path) -> None:
    assert rclone_binary(str(tmp_path / "nulle-part")) is None
