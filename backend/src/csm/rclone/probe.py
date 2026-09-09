"""Sonde rclone.

Première pierre de l'adaptateur du J2. Illustre déjà la règle du §18 :
**liste d'arguments, jamais de shell**, et aucun secret en ``argv`` — un
argument est lisible par tout processus du conteneur via ``/proc``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class RcloneInfo:
    version: str
    os: str
    arch: str


def rclone_binary() -> str | None:
    return shutil.which("rclone")


def rclone_version(timeout: float = 5.0) -> RcloneInfo | None:
    """Version de rclone, ou ``None`` si le binaire est absent ou muet.

    Enregistrée dans chaque ``task_run`` (§14) : sans elle, un diagnostic
    d'incompatibilité fournisseur est impossible à instruire.
    """
    executable = rclone_binary()
    if executable is None:
        return None

    try:
        completed = subprocess.run(
            [executable, "version", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if completed.returncode != 0:
        return None

    try:
        payload = json.loads(completed.stdout)
    except ValueError:
        return None

    return RcloneInfo(
        version=str(payload.get("version", "")),
        os=str(payload.get("os", "")),
        arch=str(payload.get("arch", "")),
    )
