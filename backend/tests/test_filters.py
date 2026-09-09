"""Filtres (FILT-001 → FILT-005, §12)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.services.filters import (
    FilterError,
    Rule,
    compile_filters,
    explain,
    parse_rules,
)
from tests.test_tasks_api import make_cloud, make_task, start, wait_for


# -- compilation --------------------------------------------------------------


def test_a_folder_without_wildcard_targets_its_contents() -> None:
    compiled = compile_filters([Rule("exclude_path", "Cache")])
    assert compiled.lines == ("- Cache/**",)


def test_a_pattern_is_kept_verbatim() -> None:
    compiled = compile_filters([Rule("exclude_path", "**/node_modules/**")])
    assert compiled.lines == ("- **/node_modules/**",)


def test_extensions_and_names() -> None:
    compiled = compile_filters(
        [Rule("exclude_ext", ".tmp"), Rule("exclude_name", "*.partial")]
    )
    assert compiled.lines == ("- *.tmp", "- *.partial")


def test_hidden_files_cover_both_levels() -> None:
    assert compile_filters([Rule("hidden", "exclude")]).lines == ("- .*", "- **/.*")


def test_an_include_rule_excludes_everything_else() -> None:
    """Sans la ligne finale, « n'inclure que les photos » ne filtrerait rien."""
    compiled = compile_filters([Rule("include_ext", "jpg")])
    assert compiled.lines == ("+ *.jpg", "- **")


def test_sizes_are_flags_not_patterns() -> None:
    compiled = compile_filters([Rule("min_size", "10M"), Rule("max_size", "2G")])
    assert compiled.lines == ()
    assert compiled.flags == ("--min-size", "10M", "--max-size", "2G")


def test_rule_order_is_preserved() -> None:
    """rclone applique la première règle qui correspond : l'ordre est du sens."""
    compiled = compile_filters(
        [Rule("include_path", "Photos/2026"), Rule("exclude_path", "Photos")]
    )
    assert compiled.lines == ("+ Photos/2026/**", "- Photos/**", "- **")


@pytest.mark.parametrize(
    "payload",
    [
        [{"type": "inconnu", "value": "x"}],
        [{"type": "exclude_path"}],
        [{"type": "min_size", "value": "beaucoup"}],
        [{"type": "hidden", "value": "include"}],
        "pas une liste",
    ],
)
def test_invalid_rules_are_refused(payload: object) -> None:
    with pytest.raises(FilterError):
        parse_rules(payload)


# -- explication (FILT-005) ---------------------------------------------------


def test_explanation_names_the_deciding_rule() -> None:
    rules = [Rule("exclude_ext", "tmp"), Rule("exclude_path", "Cache")]

    kept, reason = explain("Photos/vacances.jpg", rules)
    assert kept is True
    assert "aucune règle" in reason

    kept, reason = explain("Photos/brouillon.tmp", rules)
    assert kept is False
    assert "exclude_ext" in reason

    kept, reason = explain("Cache/gros.bin", rules)
    assert kept is False
    assert "exclude_path" in reason


def test_explanation_covers_the_implicit_exclusion() -> None:
    rules = [Rule("include_ext", "jpg")]
    kept, reason = explain("Documents/rapport.pdf", rules)
    assert kept is False
    assert "aucune règle d'inclusion" in reason


# -- API ---------------------------------------------------------------------


@pytest.mark.usefixtures("rclone_path")
def test_filter_set_crud_and_compilation(client: TestClient) -> None:
    created = client.post(
        "/api/filter-sets",
        json={
            "name": "Sans temporaires",
            "rules": [{"type": "exclude_ext", "value": "tmp"}],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["compiled"] == ["- *.tmp"]

    assert client.post(
        "/api/filter-sets", json={"name": "Sans temporaires", "rules": []}
    ).status_code == 409

    refused = client.post(
        "/api/filter-sets",
        json={"name": "Cassé", "rules": [{"type": "min_size", "value": "énorme"}]},
    )
    assert refused.status_code == 409
    assert "taille invalide" in refused.json()["detail"]

    identifier = created.json()["id"]
    assert client.delete(f"/api/filter-sets/{identifier}").status_code == 204


@pytest.mark.usefixtures("rclone_path")
def test_preview_confronts_rclone_with_the_explanation(
    client: TestClient, local_root: Path
) -> None:
    """FILT-005 — l'autorité est rclone, l'explication est indicative."""
    source = local_root / "Photos"
    (source / "Cache").mkdir(parents=True)
    (source / "vacances.jpg").write_text("photo", encoding="utf-8")
    (source / "brouillon.tmp").write_text("temporaire", encoding="utf-8")
    (source / "Cache" / "gros.bin").write_text("cache", encoding="utf-8")

    identifier = client.post(
        "/api/filter-sets",
        json={
            "name": "Propre",
            "rules": [
                {"type": "exclude_ext", "value": "tmp"},
                {"type": "exclude_path", "value": "Cache"},
            ],
        },
    ).json()["id"]

    preview = client.post(
        f"/api/filter-sets/{identifier}/preview", json={"path": str(source)}
    )
    assert preview.status_code == 200, preview.text
    payload = preview.json()

    assert payload["included"] == ["vacances.jpg"]
    excluded = {item["path"]: item["reason"] for item in payload["excluded"]}
    assert set(excluded) == {"brouillon.tmp", "Cache/gros.bin"}
    assert "exclude_ext" in excluded["brouillon.tmp"]
    assert "exclude_path" in excluded["Cache/gros.bin"]


@pytest.mark.usefixtures("rclone_path")
def test_a_filtered_task_really_skips_the_files(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    cloud = tmp_path / "cloud"
    remote_id = make_cloud(client, cloud)
    source = local_root / "Photos"
    source.mkdir()
    (source / "vacances.jpg").write_text("photo", encoding="utf-8")
    (source / "brouillon.tmp").write_text("temporaire", encoding="utf-8")

    filter_id = client.post(
        "/api/filter-sets",
        json={"name": "Sans tmp", "rules": [{"type": "exclude_ext", "value": "tmp"}]},
    ).json()["id"]

    task = make_task(client, remote_id, source)
    patched = client.patch(f"/api/tasks/{task['id']}", json={"filter_set_id": filter_id})
    assert patched.status_code == 200, patched.text
    assert patched.json()["filter_set_id"] == filter_id

    run = wait_for(client, start(client, task["id"], dry_run=False))
    assert run["status"] == "success", run
    assert run["transferred_files"] == 1
    assert (cloud / "vacances.jpg").exists()
    assert not (cloud / "brouillon.tmp").exists()

    # Le jeu appliqué reste inspectable après coup, pour le diagnostic (§14).
    applied = client.app.state.settings.config_dir / "filters" / f"{task['id']}.filter"
    assert applied.read_text(encoding="utf-8").strip() == "- *.tmp"


@pytest.mark.usefixtures("rclone_path")
def test_a_used_filter_set_cannot_be_deleted(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Photos"
    source.mkdir()
    filter_id = client.post(
        "/api/filter-sets", json={"name": "Utilisé", "rules": []}
    ).json()["id"]
    task = make_task(client, remote_id, source)
    client.patch(f"/api/tasks/{task['id']}", json={"filter_set_id": filter_id})

    refused = client.delete(f"/api/filter-sets/{filter_id}")
    assert refused.status_code == 409
    assert "utilisé" in refused.json()["detail"]


@pytest.mark.usefixtures("rclone_path")
def test_performance_settings_reach_rclone(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """PERF-001, PERF-002 — les réglages sont bien portés par la tâche."""
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Photos"
    source.mkdir()
    (source / "a.txt").write_text("un", encoding="utf-8")
    task = make_task(client, remote_id, source)

    patched = client.patch(
        f"/api/tasks/{task['id']}",
        json={"bandwidth": {"limit": "5M", "transfers": 2, "checkers": 4}},
    ).json()
    assert patched["bandwidth"] == {"limit": "5M", "transfers": 2, "checkers": 4}

    from csm.db.models import Task
    from csm.services.runner import _performance

    session = client.app.state.session_factory()
    try:
        assert _performance(session.get(Task, task["id"])) == {
            "limit": "5M",
            "transfers": 2,
            "checkers": 4,
        }
    finally:
        session.close()

    run = wait_for(client, start(client, task["id"], dry_run=False))
    assert run["status"] == "success", run
