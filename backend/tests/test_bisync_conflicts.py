"""Conflits et vérification d'accès (SYNC-003, §7.3, §8.6).

Un conflit ne détruit rien : bisync conserve les deux versions sous des noms
suffixés. Mais **le nom d'origine disparaît des deux côtés**, et c'est cela
qu'un utilisateur non prévenu prendra pour une perte. L'événement doit donc
être enregistré et visible, pas classé parmi les détails mécaniques.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.rclone import events as rclone_events
from tests.test_bisync_protocol import (  # noqa: F401 — fixture réutilisée
    make_bisync_task,
    ouvert,
    run,
    settings_of,
    wait,
)

pytestmark = pytest.mark.usefixtures("rclone_path")


def ligne(message: str, *, objet: str = "f.txt", niveau: str = "info") -> str:
    import json

    return json.dumps({"level": niveau, "msg": message, "object": objet})


# -- classement des événements ------------------------------------------------


def test_a_conflict_rename_is_recognised() -> None:
    event = rclone_events.parse_line(ligne("Moved (server-side) to: f.txt.conflict1"))
    assert event is not None
    assert event.kind == rclone_events.CONFLICT
    assert event.path == "f.txt"


def test_an_ordinary_move_is_not_a_conflict() -> None:
    event = rclone_events.parse_line(ligne("Moved (server-side) to: archive/f.txt"))
    assert event is not None
    assert event.kind == rclone_events.OTHER


def test_a_move_to_the_trash_remains_a_deletion() -> None:
    """§8.4 — la corbeille ne doit pas masquer une suppression."""
    event = rclone_events.parse_line(ligne("Moved into backup dir: f.txt"))
    assert event is not None
    assert event.kind == rclone_events.DELETE


def test_the_suffix_is_configurable() -> None:
    event = rclone_events.parse_line(
        ligne("Moved (server-side) to: f.txt.litige1"), conflict_suffix="litige"
    )
    assert event is not None
    assert event.kind == rclone_events.CONFLICT


def test_a_conflict_is_not_counted_as_a_deletion() -> None:
    """Les deux versions survivent : compter une suppression ferait franchir
    un seuil qu'aucune destruction ne justifie."""
    event = rclone_events.parse_line(ligne("Moved (server-side) to: f.txt.conflict1"))
    assert event is not None
    assert event.kind not in rclone_events.DESTRUCTIVE_KINDS


# -- conflit réel -------------------------------------------------------------


def test_a_real_conflict_keeps_both_versions_and_is_recorded(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Le comportement mesuré, de bout en bout.

    Douze fichiers plutôt que deux : au-delà de la moitié des fichiers
    modifiés, bisync avorte de lui-même — une sécurité qu'il ne faut pas
    confondre avec un échec du conflit.
    """
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    for index in range(12):
        (local / f"f{index}.txt").write_text(f"contenu {index}", encoding="utf-8")

    wait(ouvert, run(ouvert, task_id, resync=True).json()["id"])
    wait(ouvert, run(ouvert, task_id, dry_run=False, resync=True).json()["id"])

    # Le même fichier diverge des deux côtés.
    (local / "f0.txt").write_text("version locale", encoding="utf-8")
    (cloud / "f0.txt").write_text("version distante", encoding="utf-8")

    final = wait(ouvert, run(ouvert, task_id, dry_run=False).json()["id"])
    assert final["status"] in {"success", "warning"}, final

    noms_local = {f.name for f in local.iterdir()}
    noms_cloud = {f.name for f in cloud.iterdir()}

    # Rien n'est perdu : les deux versions survivent, des deux côtés.
    assert {"f0.txt.conflict1", "f0.txt.conflict2"} <= noms_local
    assert {"f0.txt.conflict1", "f0.txt.conflict2"} <= noms_cloud
    # Mais le nom d'origine a disparu : c'est ce qui alarme sans raison.
    assert "f0.txt" not in noms_local

    conflits = ouvert.get(
        f"/api/runs/{final['id']}/events", params={"kind": "conflict"}
    ).json()
    assert conflits, "un conflit doit laisser une trace consultable"
    assert any("f0.txt" in (event["path"] or "") for event in conflits)


# -- vérification d'accès -----------------------------------------------------


def test_markers_are_placed_on_both_sides(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """§8.6 — un mécanisme de vérification d'accès aux deux côtés."""
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)

    placed = ouvert.post(f"/api/tasks/{task_id}/bisync/markers")
    assert placed.status_code == 200, placed.text
    assert set(placed.json()) == {"path1", "path2"}
    assert (local / "RCLONE_TEST").exists()
    assert (cloud / "RCLONE_TEST").exists()


def test_markers_are_refused_on_a_one_way_task(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    from tests.test_tasks_api import make_cloud, make_task

    remote_id = make_cloud(ouvert, tmp_path / "ailleurs")
    task = make_task(ouvert, remote_id, local_root, name="Copie simple")

    refused = ouvert.post(f"/api/tasks/{task['id']}/bisync/markers")
    assert refused.status_code == 409
    assert "bidirectionnelles" in refused.json()["detail"]


def test_the_access_check_refuses_a_side_without_its_marker(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Un côté monté mais vide ne doit pas passer pour un côté accessible."""
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    (local / "a.txt").write_text("a", encoding="utf-8")

    ouvert.patch(f"/api/tasks/{task_id}/bisync", json={"check_access": True})
    ouvert.post(f"/api/tasks/{task_id}/bisync/markers")
    (cloud / "RCLONE_TEST").unlink()

    refused = wait(ouvert, run(ouvert, task_id, resync=True).json()["id"])
    assert refused["status"] != "success"


# -- politique de conflit -----------------------------------------------------


def test_the_default_policy_destroys_nothing(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    task_id = make_bisync_task(ouvert, local_root / "local", tmp_path / "cloud")
    reglages = ouvert.get(f"/api/tasks/{task_id}/bisync").json()
    assert reglages["conflict_resolve"] == "none"
    assert reglages["conflict_loser"] == "num"
    assert reglages["check_access"] is False


def test_the_policy_can_be_changed(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    task_id = make_bisync_task(ouvert, local_root / "local", tmp_path / "cloud")
    updated = ouvert.patch(
        f"/api/tasks/{task_id}/bisync", json={"conflict_resolve": "newer"}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["conflict_resolve"] == "newer"
    assert updated.json()["conflict_loser"] == "num", "le reste ne bouge pas"


@pytest.mark.parametrize(
    ("champ", "valeur"),
    [("conflict_resolve", "au-hasard"), ("conflict_loser", "au-hasard")],
)
def test_an_unknown_policy_is_refused(
    ouvert: TestClient,
    tmp_path: Path,
    local_root: Path,
    champ: str,
    valeur: str,
) -> None:
    task_id = make_bisync_task(ouvert, local_root / "local", tmp_path / "cloud")
    refused = ouvert.patch(f"/api/tasks/{task_id}/bisync", json={champ: valeur})
    assert refused.status_code == 422


def test_the_initialisation_state_is_not_declarable(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """§7.3 — la référence s'établit par une ré-initialisation, pas par une
    déclaration. Sans quoi il suffirait de mentir pour contourner le garde-fou."""
    task_id = make_bisync_task(ouvert, local_root / "local", tmp_path / "cloud")
    ouvert.patch(f"/api/tasks/{task_id}/bisync", json={"initialised": True})
    assert settings_of(ouvert, task_id)["initialised"] is False
