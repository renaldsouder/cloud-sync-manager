"""Lecture du journal JSON de rclone (§8.4, LOG-002, UI-003).

rclone écrit **tout** sur ``stderr`` avec ``--use-json-log`` ; ``stdout``
reste vide. Deux natures de lignes nous intéressent :

- une opération sur un fichier — ``{"msg": "Copied (new)", "object": "a.txt"}``
  ou, en simulation, ``{"skipped": "delete", "object": "vieux.txt"}`` ;
- un relevé périodique — la même ligne porte alors un objet ``stats``.

C'est la simulation qui rend le §8.3 possible : elle nomme un par un les
fichiers qui *seraient* supprimés, ce qui permet à la fois de compter et
d'afficher la liste avant d'autoriser quoi que ce soit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

#: Familles d'événements enregistrées dans ``task_events``.
TRANSFER = "transfer"
DELETE = "delete"
SKIP_TRANSFER = "skip_transfer"
SKIP_DELETE = "skip_delete"
#: Les deux versions d'un fichier modifié des deux côtés ont été conservées
#: sous des noms suffixés (§7.3). Rien n'est perdu, mais le nom d'origine
#: disparaît : l'utilisateur doit le voir, sans quoi il croira le fichier
#: volatilisé.
CONFLICT = "conflict"
ERROR = "error"
STATS = "stats"
OTHER = "other"

#: Événements comptés comme destructifs, en simulation comme en réel (§8.3).
DESTRUCTIVE_KINDS = frozenset({DELETE, SKIP_DELETE})


@dataclass(frozen=True)
class RcloneEvent:
    kind: str
    level: str
    message: str
    path: str | None = None
    size: int | None = None
    stats: dict[str, Any] | None = None


def parse_line(line: str, *, conflict_suffix: str = "conflict") -> RcloneEvent | None:
    """Transforme une ligne de journal en événement, ou ``None`` si illisible.

    Une ligne non JSON n'est jamais fatale : rclone peut écrire du texte
    avant que la journalisation structurée ne soit installée.

    ``conflict_suffix`` est celui passé à bisync : c'est lui qui permet de
    distinguer le renommage d'une version en conflit d'un déplacement
    ordinaire. bisync annonce aussi « Renaming Path1 copy », mais le chemin
    qu'il y affiche est amputé de son extension — la ligne « Moved » porte
    le nom exact.
    """
    stripped = line.strip()
    if not stripped or not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None

    level = str(payload.get("level", "info"))
    message = str(payload.get("msg", "")).strip()
    path = payload.get("object")
    size = payload.get("size")

    return RcloneEvent(
        kind=_classify(payload, level, message, conflict_suffix),
        level=level,
        message=message,
        path=str(path) if path else None,
        size=int(size) if isinstance(size, int) else None,
        stats=payload.get("stats") if isinstance(payload.get("stats"), dict) else None,
    )


def _classify(
    payload: dict[str, Any], level: str, message: str, conflict_suffix: str
) -> str:
    if isinstance(payload.get("stats"), dict):
        return STATS

    skipped = payload.get("skipped")
    if skipped == "delete":
        return SKIP_DELETE
    if skipped in {"copy", "move", "update"}:
        return SKIP_TRANSFER

    if level in {"error", "critical"}:
        return ERROR

    lowered = message.lower()

    # Avec une quarantaine (``--backup-dir``), rclone ne supprime pas : il
    # déplace, et n'émet donc jamais « Deleted ». Sans ce cas, aucune
    # suppression ne serait tracée dès que la corbeille est active (§8.4).
    if lowered.startswith("moved into backup dir"):
        return DELETE
    if lowered.startswith("moved") and f".{conflict_suffix.lower()}" in lowered:
        return CONFLICT
    if lowered.startswith("moved"):
        # Moitié mécanique du déplacement : la ligne ci-dessus porte déjà le
        # sens, la compter aussi ferait un doublon.
        return OTHER

    if lowered.startswith("deleted"):
        return DELETE
    if lowered.startswith(("copied", "updated", "renamed")):
        return TRANSFER

    return OTHER


def summarise(stats: dict[str, Any]) -> dict[str, Any]:
    """Extrait du relevé rclone ce que l'UI et l'historique exploitent."""
    return {
        "bytes": int(stats.get("bytes") or 0),
        "total_bytes": int(stats.get("totalBytes") or 0),
        "transfers": int(stats.get("transfers") or 0),
        "total_transfers": int(stats.get("totalTransfers") or 0),
        "checks": int(stats.get("checks") or 0),
        "deletes": int(stats.get("deletes") or 0),
        "errors": int(stats.get("errors") or 0),
        "speed": float(stats.get("speed") or 0.0),
        "eta": stats.get("eta"),
        "elapsed": float(stats.get("elapsedTime") or 0.0),
        "fatal_error": bool(stats.get("fatalError")),
    }
