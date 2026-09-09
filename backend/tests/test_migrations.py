"""DATA-004 / UPDATE-002 — les migrations doivent être vérifiables.

Le §27.7 impose un test de migration à chaque révision. Pour la révision
initiale, on vérifie l'aller (schéma complet), le retour (base vide) et
l'aller-retour, afin que la 0002 ait déjà un socle sur lequel s'appuyer.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect

from csm.db.base import sqlite_url
from csm.db.migrate import downgrade_to, upgrade_to_head

EXPECTED_TABLES = {
    "remotes",
    "filter_sets",
    "tasks",
    "task_runs",
    "task_events",
    "settings",
}


def _tables(db_path: Path) -> set[str]:
    engine = create_engine(sqlite_url(db_path))
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_upgrade_from_empty_creates_full_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "csm.sqlite"
    upgrade_to_head(sqlite_url(db_path))
    assert EXPECTED_TABLES <= _tables(db_path)


def test_downgrade_removes_application_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "csm.sqlite"
    url = sqlite_url(db_path)
    upgrade_to_head(url)
    downgrade_to(url, "base")
    assert not (EXPECTED_TABLES & _tables(db_path))


def test_upgrade_is_idempotent_after_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "csm.sqlite"
    url = sqlite_url(db_path)
    upgrade_to_head(url)
    downgrade_to(url, "base")
    upgrade_to_head(url)
    assert EXPECTED_TABLES <= _tables(db_path)


def test_tasks_defaults_are_non_destructive(tmp_path: Path) -> None:
    """CONF-002 : la politique par défaut ne doit jamais propager une suppression."""
    db_path = tmp_path / "csm.sqlite"
    upgrade_to_head(sqlite_url(db_path))
    engine = create_engine(sqlite_url(db_path))
    try:
        columns = {c["name"]: c for c in inspect(engine).get_columns("tasks")}
    finally:
        engine.dispose()

    assert "'never'" in str(columns["delete_policy"]["default"])
    assert "1" in str(columns["quarantine_enabled"]["default"])
    assert "1" in str(columns["dry_run_required"]["default"])
