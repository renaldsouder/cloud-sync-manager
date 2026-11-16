"""Réglages de transfert d'une tâche (PERF-001, PERF-002, SYNC-005).

Ils sont stockés dans la colonne ``bandwidth_json`` de la tâche, qui porte
depuis l'origine tout ce qui règle *comment* rclone transfère, par
opposition à *quoi* et *où* :

- ``transfers`` — transferts simultanés (PERF-001) ;
- ``checkers`` — vérifications simultanées (§13) ;
- ``limit`` — bande passante, au format rclone : ``10M``, ``500k``, ou
  ``2M:1M`` pour montant:descendant (PERF-002) ;
- ``checksum`` — comparer par somme de contrôle plutôt que par taille et
  date (SYNC-005).

Tout passe par :func:`normalise` avant d'être enregistré, y compris à la
restauration d'un export : ces valeurs finissent dans la ligne de commande
d'une opération qui peut être destructive, et une valeur qui ressemble à une
option (``--delete-before``) ne doit jamais y arriver.
"""

from __future__ import annotations

import re
from typing import Any

#: Bornes volontairement larges, mais finies : au-delà, on ne règle plus un
#: transfert, on fait tomber le fournisseur en limitation de débit.
MAX_PARALLEL = 64

#: Une valeur de bande passante rclone : nombre, suffixe d'unité facultatif,
#: et éventuellement une seconde valeur pour le sens descendant.
_RATE = r"(?:\d+(?:\.\d+)?[BbKkMmGgTtPp]?|off)"
_LIMIT = re.compile(rf"^{_RATE}(?::{_RATE})?$")

KNOWN_KEYS = frozenset({"transfers", "checkers", "limit", "checksum"})


class PerformanceError(ValueError):
    """Réglage de transfert refusé, message destiné à l'utilisateur."""


def normalise(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Valide et nettoie les réglages. ``None`` quand il ne reste rien.

    Une clé inconnue est refusée plutôt qu'ignorée : un réglage qu'on croit
    appliqué et qui ne l'est pas est pire qu'un refus.
    """
    if not payload:
        return None
    if not isinstance(payload, dict):
        raise PerformanceError("réglages de transfert illisibles")

    unknown = sorted(set(payload) - KNOWN_KEYS)
    if unknown:
        raise PerformanceError(f"réglage de transfert inconnu : {', '.join(unknown)}")

    cleaned: dict[str, Any] = {}
    for key, label in (
        ("transfers", "transferts simultanés"),
        ("checkers", "vérifications simultanées"),
    ):
        value = payload.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise PerformanceError(f"{label} : un nombre entier est attendu")
        if not 1 <= value <= MAX_PARALLEL:
            raise PerformanceError(f"{label} : entre 1 et {MAX_PARALLEL}")
        cleaned[key] = value

    limit = payload.get("limit")
    if limit not in (None, ""):
        text = str(limit).strip()
        if not _LIMIT.match(text):
            raise PerformanceError(
                "limite de bande passante illisible : par exemple 10M, 500k, "
                "ou 2M:1M pour montant:descendant"
            )
        cleaned["limit"] = text

    checksum = payload.get("checksum")
    if checksum is not None:
        if not isinstance(checksum, bool):
            raise PerformanceError("comparaison par somme de contrôle : oui ou non")
        if checksum:
            cleaned["checksum"] = True

    return cleaned or None


def lenient(payload: Any) -> dict[str, Any]:
    """Réglages relus en base, sans jamais faire échouer une exécution.

    Ce qui est enregistré a déjà été validé ; une valeur devenue illisible
    — base éditée à la main, version ultérieure — est simplement ignorée,
    ce qui revient au comportement par défaut de rclone.
    """
    if not isinstance(payload, dict):
        return {}
    settings: dict[str, Any] = {}
    for key in ("transfers", "checkers"):
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_PARALLEL:
            settings[key] = value
    limit = payload.get("limit")
    if isinstance(limit, str) and _LIMIT.match(limit.strip()):
        settings["limit"] = limit.strip()
    if payload.get("checksum") is True:
        settings["checksum"] = True
    return settings


def common_hashes(first: list[str] | None, second: list[str] | None) -> list[str]:
    """Sommes de contrôle connues des deux côtés.

    rclone ne compare par somme de contrôle que sur un algorithme commun.
    Sans algorithme commun, ``--checksum`` retombe sur une comparaison **par
    taille seule** : un fichier modifié sans changer de taille ne serait plus
    jamais recopié. C'est précisément ce que l'appelant doit empêcher.
    """
    if not first or not second:
        return []
    other = set(second)
    return [name for name in first if name in other]
