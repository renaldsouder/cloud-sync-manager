"""Sonde rclone.

Première pierre de l'adaptateur du J2. Illustre déjà la règle du §18 :
**liste d'arguments, jamais de shell**, et aucun secret en ``argv`` — un
argument est lisible par tout processus du conteneur via ``/proc``.

La version est lue par ``rc --loopback core/version`` plutôt qu'en analysant
la sortie texte de ``rclone version`` : ``--loopback`` exécute la commande
rc dans le processus courant, sans démon, et rend du JSON stable. Cela ne
contredit pas la décision P3 — c'est bien un sous-processus par appel, avec
des arguments en liste. (``rclone version`` n'accepte pas ``--json``.)
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RcloneInfo:
    version: str
    os: str
    arch: str
    go_version: str = ""


def rclone_binary(configured: str | None = None) -> str | None:
    """Chemin du binaire : celui configuré, sinon celui du ``PATH``.

    En production l'image en fournit un à version épinglée ; en
    développement, ``CSM_RCLONE_BINARY`` permet de pointer une copie locale.
    """
    if configured:
        candidate = Path(configured)
        return str(candidate) if candidate.is_file() else None
    return shutil.which("rclone")


def rclone_version(
    configured: str | None = None, timeout: float = 10.0
) -> RcloneInfo | None:
    """Version de rclone, ou ``None`` si le binaire est absent ou muet.

    Enregistrée dans chaque ``task_run`` (§14) : sans elle, un diagnostic
    d'incompatibilité fournisseur est impossible à instruire.
    """
    executable = rclone_binary(configured)
    if executable is None:
        return None

    try:
        completed = subprocess.run(
            [executable, "rc", "--loopback", "core/version"],
            capture_output=True,
            text=True,
            # rclone émet de l'UTF-8 quelle que soit la locale de l'hôte.
            encoding="utf-8",
            errors="replace",
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
        go_version=str(payload.get("goVersion", "")),
    )
