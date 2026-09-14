"""Parcours de l'arborescence distante (CLOUD-005, §3).

Une faute de frappe dans un dossier distant ne se voit qu'à la première
exécution, sur un dossier créé par erreur — et la corriger suppose de
retrouver puis d'effacer ce qui a été envoyé au mauvais endroit.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_tasks_api import make_cloud

pytestmark = pytest.mark.usefixtures("rclone_path")


def parcourir(client: TestClient, remote_id: str, chemin: str | None = None):
    params = {"path": chemin} if chemin else None
    return client.get(f"/api/remotes/{remote_id}/browse", params=params)


@pytest.fixture
def stockage(client: TestClient, tmp_path: Path) -> tuple[str, Path]:
    cloud = tmp_path / "cloud"
    remote_id = make_cloud(client, cloud)
    return remote_id, cloud


def test_the_root_lists_the_folders(
    client: TestClient, stockage: tuple[str, Path]
) -> None:
    remote_id, cloud = stockage
    for nom in ("Sauvegardes", "Photos"):
        (cloud / nom).mkdir(parents=True, exist_ok=True)

    listing = parcourir(client, remote_id).json()
    assert listing["path"] == ""
    assert {e["name"] for e in listing["entries"]} == {"Sauvegardes", "Photos"}
    assert listing["parent"] is None, "la racine n'a pas de parent"


def test_files_are_not_listed(client: TestClient, stockage: tuple[str, Path]) -> None:
    """On choisit un dossier de destination, pas un fichier."""
    remote_id, cloud = stockage
    (cloud / "Photos").mkdir(parents=True, exist_ok=True)
    (cloud / "note.txt").write_text("x", encoding="utf-8")

    noms = {e["name"] for e in parcourir(client, remote_id).json()["entries"]}
    assert noms == {"Photos"}


def test_a_subfolder_can_be_entered(
    client: TestClient, stockage: tuple[str, Path]
) -> None:
    remote_id, cloud = stockage
    (cloud / "Photos" / "2026").mkdir(parents=True, exist_ok=True)

    listing = parcourir(client, remote_id, "Photos").json()
    assert listing["path"] == "Photos"
    assert [e["name"] for e in listing["entries"]] == ["2026"]
    assert [e["path"] for e in listing["entries"]] == ["Photos/2026"]
    assert listing["parent"] == "", "on remonte vers la racine"


def test_the_parent_of_a_nested_folder_is_its_own_parent(
    client: TestClient, stockage: tuple[str, Path]
) -> None:
    remote_id, cloud = stockage
    (cloud / "Photos" / "2026" / "Été").mkdir(parents=True, exist_ok=True)

    listing = parcourir(client, remote_id, "Photos/2026").json()
    assert listing["parent"] == "Photos"
    assert [e["path"] for e in listing["entries"]] == ["Photos/2026/Été"]


def test_accented_names_survive(
    client: TestClient, stockage: tuple[str, Path]
) -> None:
    """GLOBAL-005 — déjà corrigé une fois sur le décodage de rclone."""
    remote_id, cloud = stockage
    (cloud / "Été à Noël").mkdir(parents=True, exist_ok=True)

    noms = {e["name"] for e in parcourir(client, remote_id).json()["entries"]}
    assert "Été à Noël" in noms


def test_entries_are_sorted_case_insensitively(
    client: TestClient, stockage: tuple[str, Path]
) -> None:
    remote_id, cloud = stockage
    for nom in ("zèbre", "Alpha", "brouillon"):
        (cloud / nom).mkdir(parents=True, exist_ok=True)

    noms = [e["name"] for e in parcourir(client, remote_id).json()["entries"]]
    assert noms == ["Alpha", "brouillon", "zèbre"]


def test_an_empty_storage_is_not_an_error(
    client: TestClient, stockage: tuple[str, Path]
) -> None:
    """Un stockage neuf est vide : c'est le cas normal au premier usage."""
    remote_id, _ = stockage
    listing = parcourir(client, remote_id)
    assert listing.status_code == 200
    assert listing.json()["entries"] == []


def test_climbing_above_the_root_is_refused(
    client: TestClient, stockage: tuple[str, Path]
) -> None:
    """Rien ne garantit qu'un fournisseur traiterait « .. » comme un système
    de fichiers local."""
    remote_id, _ = stockage
    refuse = parcourir(client, remote_id, "../../etc")
    assert refuse.status_code == 409


def test_an_unknown_storage_is_refused(client: TestClient) -> None:
    assert parcourir(client, "inexistant").status_code == 404


def test_a_missing_folder_reports_the_cause(
    client: TestClient, stockage: tuple[str, Path]
) -> None:
    """§27.10 — le motif de rclone vaut mieux qu'un « échec »."""
    remote_id, _ = stockage
    manquant = parcourir(client, remote_id, "jamais-cree")
    assert manquant.status_code == 409
    assert manquant.json()["detail"]
