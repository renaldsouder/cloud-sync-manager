"""Validation des chemins locaux (LOCAL-003, LOCAL-005, §18).

Deux garanties distinctes :

1. **Confinement** — un chemin doit se résoudre à l'intérieur d'une racine
   montée et autorisée. La résolution passe par ``realpath`` : un lien
   symbolique qui sort de la racine est rejeté, pas suivi.
2. **Destination protégée** — certains emplacements ne peuvent jamais être la
   cible d'une écriture, a fortiori d'un Miroir Cloud → Local (P13, §8).
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

#: Shares Unraid qu'une tâche ne doit jamais prendre pour destination.
#: Un Miroir descendant sur ``appdata`` détruirait la configuration de toutes
#: les applications du serveur.
FORBIDDEN_SHARES = frozenset({"appdata", "system", "domains"})


class PathNotAllowed(ValueError):
    """Le chemin sort des racines autorisées ou vise une destination protégée."""


def resolve_within_roots(candidate: str | os.PathLike[str], roots: tuple[Path, ...]) -> Path:
    """Résout ``candidate`` et vérifie qu'il reste sous l'une des ``roots``.

    Renvoie le chemin résolu. Lève :class:`PathNotAllowed` sinon.
    """
    if not roots:
        raise PathNotAllowed("aucune racine locale n'est autorisée")

    raw = Path(candidate)
    if not raw.is_absolute():
        raise PathNotAllowed(f"chemin relatif refusé : {candidate!r}")

    resolved = Path(os.path.realpath(raw))
    for root in roots:
        resolved_root = Path(os.path.realpath(root))
        if resolved == resolved_root or _is_relative_to(resolved, resolved_root):
            return resolved

    raise PathNotAllowed(
        f"{raw} sort des racines autorisées ({', '.join(str(r) for r in roots)})"
    )


def assert_valid_destination(resolved: Path, roots: tuple[Path, ...]) -> None:
    """Refuse les destinations d'écriture interdites (P13).

    À appeler en **création de tâche**, pas à l'exécution : l'utilisateur doit
    être arrêté au moment où il configure, pas après avoir planifié.
    """
    for root in roots:
        resolved_root = Path(os.path.realpath(root))
        if resolved == resolved_root:
            raise PathNotAllowed(
                f"{resolved} est une racine : une destination doit désigner un "
                "share ou un dossier précis"
            )
        if _is_relative_to(resolved, resolved_root):
            share = resolved.relative_to(resolved_root).parts[0]
            if share.lower() in FORBIDDEN_SHARES:
                raise PathNotAllowed(
                    f"le share « {share} » ne peut pas être une destination "
                    "de synchronisation"
                )
            return

    raise PathNotAllowed(f"{resolved} sort des racines autorisées")


def is_writable(path: Path) -> bool:
    """Droits d'écriture effectifs sur ``path`` (LOCAL-003)."""
    return os.access(path, os.W_OK)


def paths_overlap(first: Path, second: Path) -> bool:
    """Vrai si l'un des chemins contient l'autre.

    Deux tâches dont les chemins locaux s'imbriquent — surtout en sens
    opposés — peuvent se renvoyer des suppressions indéfiniment (P13, §8.6).
    """
    return first == second or _is_relative_to(first, second) or _is_relative_to(second, first)


def to_remote_path(value: str) -> str:
    """Normalise un chemin distant : séparateurs POSIX, pas de ``..``."""
    pure = PurePosixPath(value.replace("\\", "/"))
    if ".." in pure.parts:
        raise PathNotAllowed(f"chemin distant invalide : {value!r}")
    return pure.as_posix().lstrip("/")


def _is_relative_to(path: Path, other: Path) -> bool:
    try:
        path.relative_to(other)
    except ValueError:
        return False
    return True
