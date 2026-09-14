"""Parcours des dossiers locaux (LOCAL-002, LOCAL-005).

Ce parcours ouvre une lecture du système de fichiers à quiconque atteint
l'interface. Il doit donc respecter exactement les mêmes bornes que la
création de tâche : c'est ce que vérifie la moitié de ce fichier.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def lister(client: TestClient, chemin: str | None = None):
    params = {"path": chemin} if chemin else None
    return client.get("/api/local/browse", params=params)


# -- ce que l'utilisateur vient chercher --------------------------------------


def test_the_root_lists_the_shares(client: TestClient, local_root: Path) -> None:
    for nom in ("Photos", "Documents", "Films"):
        (local_root / nom).mkdir(parents=True, exist_ok=True)

    listing = lister(client).json()
    assert listing["path"] == str(local_root)
    assert {e["name"] for e in listing["entries"]} == {
        "Photos",
        "Documents",
        "Films",
    }


def test_the_root_has_no_parent(client: TestClient, local_root: Path) -> None:
    """Remonter au-dessus de la racine n'aurait aucun sens et serait une fuite."""
    assert lister(client).json()["parent"] is None


def test_a_subfolder_can_be_entered(client: TestClient, local_root: Path) -> None:
    (local_root / "Photos" / "2026").mkdir(parents=True, exist_ok=True)

    listing = lister(client, str(local_root / "Photos")).json()
    assert [e["name"] for e in listing["entries"]] == ["2026"]
    assert listing["parent"] == str(local_root)


def test_files_are_not_listed(client: TestClient, local_root: Path) -> None:
    """On choisit un dossier à synchroniser, pas un fichier."""
    (local_root / "Photos").mkdir(parents=True, exist_ok=True)
    (local_root / "note.txt").write_text("x", encoding="utf-8")

    noms = {e["name"] for e in lister(client).json()["entries"]}
    assert noms == {"Photos"}


def test_entries_are_sorted_case_insensitively(
    client: TestClient, local_root: Path
) -> None:
    for nom in ("zèbre", "Alpha", "brouillon"):
        (local_root / nom).mkdir(parents=True, exist_ok=True)

    noms = [e["name"] for e in lister(client).json()["entries"]]
    assert noms == ["Alpha", "brouillon", "zèbre"]


def test_accented_names_survive(client: TestClient, local_root: Path) -> None:
    """GLOBAL-005 — un nom accentué doit traverser l'API intact."""
    (local_root / "Été à Noël").mkdir(parents=True, exist_ok=True)
    noms = {e["name"] for e in lister(client).json()["entries"]}
    assert "Été à Noël" in noms


# -- les bornes ---------------------------------------------------------------


def test_a_path_outside_the_roots_is_refused(
    client: TestClient, tmp_path: Path
) -> None:
    """LOCAL-005 — le parcours ne doit pas être une porte dérobée."""
    dehors = tmp_path / "dehors"
    dehors.mkdir(parents=True, exist_ok=True)

    refuse = lister(client, str(dehors))
    assert refuse.status_code == 409
    assert "racines autoris" in refuse.json()["detail"]


def test_climbing_out_with_dot_dot_is_refused(
    client: TestClient, local_root: Path
) -> None:
    refuse = lister(client, str(local_root / ".." / ".." / ".."))
    assert refuse.status_code == 409


def test_a_relative_path_is_refused(client: TestClient) -> None:
    assert lister(client, "Photos").status_code == 409


def test_a_missing_folder_says_so(client: TestClient, local_root: Path) -> None:
    """§27.10 — le message doit désigner la cause probable."""
    manquant = lister(client, str(local_root / "jamais-cree"))
    assert manquant.status_code == 404
    assert "montage" in manquant.json()["detail"]


# -- ce que l'interface doit pouvoir montrer ----------------------------------


def test_forbidden_shares_are_shown_but_flagged(
    client: TestClient, local_root: Path
) -> None:
    """Les masquer laisserait croire qu'ils n'existent pas.

    L'utilisateur les chercherait alors, au lieu de comprendre qu'ils sont
    refusés comme destination (P13).
    """
    for nom in ("appdata", "system", "domains", "Photos"):
        (local_root / nom).mkdir(parents=True, exist_ok=True)

    par_nom = {e["name"]: e for e in lister(client).json()["entries"]}
    assert set(par_nom) == {"appdata", "system", "domains", "Photos"}
    assert par_nom["appdata"]["forbidden_destination"] is True
    assert par_nom["system"]["forbidden_destination"] is True
    assert par_nom["Photos"]["forbidden_destination"] is False


def test_a_nested_folder_of_a_forbidden_share_stays_flagged(
    client: TestClient, local_root: Path
) -> None:
    (local_root / "appdata" / "sous").mkdir(parents=True, exist_ok=True)

    entrees = lister(client, str(local_root / "appdata")).json()["entries"]
    assert entrees[0]["forbidden_destination"] is True


def test_the_roots_are_announced(client: TestClient, local_root: Path) -> None:
    """LOCAL-004 — plusieurs racines sont possibles ; l'interface doit savoir
    lesquelles."""
    assert lister(client).json()["roots"] == [str(local_root)]


@pytest.mark.parametrize("nombre", [3, 12])
def test_the_listing_is_not_truncated_for_ordinary_folders(
    client: TestClient, local_root: Path, nombre: int
) -> None:
    for index in range(nombre):
        (local_root / f"share{index:03d}").mkdir(parents=True, exist_ok=True)

    listing = lister(client).json()
    assert listing["truncated"] is False
    assert len(listing["entries"]) == nombre
