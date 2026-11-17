"""DATA-004 / UPDATE-002 — les migrations doivent être vérifiables.

Le §27.7 impose un test de migration à chaque révision : l'aller, le retour
et l'aller-retour.
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


def _task_columns(db_path: Path) -> dict[str, dict]:
    engine = create_engine(sqlite_url(db_path))
    try:
        return {c["name"]: c for c in inspect(engine).get_columns("tasks")}
    finally:
        engine.dispose()


def test_0002_adds_the_bidirectional_settings(tmp_path: Path) -> None:
    """SYNC-003 — les réglages du bidirectionnel tiennent dans une colonne."""
    db_path = tmp_path / "csm.sqlite"
    upgrade_to_head(sqlite_url(db_path))
    assert "bisync_json" in _task_columns(db_path)


def test_0002_can_be_rolled_back(tmp_path: Path) -> None:
    """Une révision qu'on ne sait pas défaire n'est pas une révision."""
    db_path = tmp_path / "csm.sqlite"
    url = sqlite_url(db_path)
    upgrade_to_head(url)
    downgrade_to(url, "0001")

    colonnes = _task_columns(db_path)
    assert "bisync_json" not in colonnes
    # Le reste de la table doit survivre au retour en arrière.
    assert {"id", "name", "mode", "delete_policy"} <= set(colonnes)


def test_0002_survives_a_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "csm.sqlite"
    url = sqlite_url(db_path)
    upgrade_to_head(url)
    downgrade_to(url, "0001")
    upgrade_to_head(url)
    assert "bisync_json" in _task_columns(db_path)


def test_0003_adds_one_shot_and_keeps_existing_tasks_permanent(tmp_path: Path) -> None:
    """SYNC-008 — une tâche créée avant la migration reste une tâche
    permanente : elle ne doit pas disparaître de la liste."""
    from sqlalchemy import text

    db_path = tmp_path / "csm.sqlite"
    url = sqlite_url(db_path)
    upgrade_to_head(url)
    downgrade_to(url, "0002")

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO remotes (id, name, provider, rclone_remote_name, status, "
                    "created_at, updated_at) VALUES ('r', 'R', 'alias', 'R', 'ok', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO tasks (id, name, remote_id, local_path, remote_path, "
                    "direction, mode, delete_policy, quarantine_enabled, "
                    "dry_run_required, enabled, status, created_at, updated_at) "
                    "VALUES ('t', 'T', 'r', '/mnt/user/a', '', 'local_to_remote', "
                    "'mirror', 'confirm', 1, 1, 1, 'ready', CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP)"
                )
            )
    finally:
        engine.dispose()

    upgrade_to_head(url)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT one_shot FROM tasks")).scalar() == 0
    finally:
        engine.dispose()


def test_0003_can_be_rolled_back(tmp_path: Path) -> None:
    db_path = tmp_path / "csm.sqlite"
    url = sqlite_url(db_path)
    upgrade_to_head(url)
    downgrade_to(url, "0002")
    assert "one_shot" not in _task_columns(db_path)
    upgrade_to_head(url)
    assert "one_shot" in _task_columns(db_path)


def test_a_database_from_a_newer_version_is_refused(tmp_path: Path) -> None:
    """AIDE-004, UPDATE-003 — revenir à une image plus ancienne sans
    restaurer l'appdata laisse une base migrée par une version plus récente.
    Le démarrage doit échouer plutôt que de travailler sur un schéma qu'il ne
    connaît pas : c'est ce que la procédure de restauration documente."""
    import pytest
    from alembic.util.exc import CommandError
    from sqlalchemy import text

    db = tmp_path / "csm.sqlite"
    upgrade_to_head(sqlite_url(db))
    engine = create_engine(sqlite_url(db))
    try:
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = '9999_futur'"))
    finally:
        engine.dispose()

    with pytest.raises(CommandError):
        upgrade_to_head(sqlite_url(db))
