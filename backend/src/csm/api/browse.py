"""Parcours des dossiers locaux (LOCAL-002, LOCAL-005, §3).

Le §3 demande à l'utilisateur de « choisir un dossier/share local accessible
au conteneur ». Jusqu'ici il fallait **taper** le chemin à l'aveugle, sans
savoir si le share existait ni s'il était accessible — dans un produit dont la
promesse est de se passer de ligne de commande.

Ce parcours ne peut pas servir d'évasion : tout chemin demandé passe par la
même résolution que la création de tâche, et une racine non autorisée est
refusée avant même d'être listée.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from csm.api.deps import get_settings_dep
from csm.config import Settings
from csm.paths import (
    FORBIDDEN_SHARES,
    PathNotAllowed,
    is_writable,
    resolve_within_roots,
)

router = APIRouter(tags=["dossiers"])

#: Au-delà, la liste cesse d'être utile et le rendu devient coûteux. Un
#: dossier de cette taille se désigne en tapant son chemin.
MAX_ENTRIES = 500


class Entry(BaseModel):
    name: str
    path: str
    #: Écriture possible : une destination Cloud → Local en a besoin.
    writable: bool = True
    #: Share qui ne peut jamais être une destination (P13).
    forbidden_destination: bool = False


class Listing(BaseModel):
    path: str
    #: ``None`` lorsqu'on est déjà sur une racine autorisée.
    parent: str | None = None
    roots: list[str] = []
    entries: list[Entry] = []
    truncated: bool = False


def _is_root(resolved: Path, settings: Settings) -> bool:
    return any(
        resolved == Path(os.path.realpath(root)) for root in settings.roots
    )


@router.get("/local/browse", response_model=Listing)
def browse_local(
    path: str | None = None, settings: Settings = Depends(get_settings_dep)
) -> Listing:
    roots = settings.roots
    if not roots:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "aucune racine locale n'est autorisée"
        )

    cible = path or str(roots[0])
    try:
        resolved = resolve_within_roots(cible, roots)
    except PathNotAllowed as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    if not resolved.is_dir():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"{resolved} n'existe pas ou n'est pas un dossier. Vérifiez le "
            "montage du conteneur.",
        )

    try:
        enfants = sorted(
            (child for child in resolved.iterdir() if child.is_dir()),
            key=lambda child: child.name.lower(),
        )
    except PermissionError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{resolved} n'est pas lisible par le conteneur : vérifiez PUID, "
            "PGID et le montage.",
        ) from exc

    tronque = len(enfants) > MAX_ENTRIES
    entries = [
        Entry(
            name=child.name,
            path=str(child),
            writable=is_writable(child),
            # Un share interdit reste listé : le masquer laisserait croire
            # qu'il n'existe pas, et l'utilisateur le chercherait.
            forbidden_destination=_forbidden(child, settings),
        )
        for child in enfants[:MAX_ENTRIES]
    ]

    return Listing(
        path=str(resolved),
        parent=None if _is_root(resolved, settings) else str(resolved.parent),
        roots=[str(root) for root in roots],
        entries=entries,
        truncated=tronque,
    )


def _forbidden(child: Path, settings: Settings) -> bool:
    for root in settings.roots:
        resolved_root = Path(os.path.realpath(root))
        try:
            relatif = Path(os.path.realpath(child)).relative_to(resolved_root)
        except ValueError:
            continue
        if relatif.parts and relatif.parts[0].lower() in FORBIDDEN_SHARES:
            return True
    return False
