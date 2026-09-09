"""Schéma initial (§6)

Revision ID: 0001
Revises:
Create Date: 2026-09-09
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "remotes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("rclone_remote_name", sa.String(128), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("last_test_at", sa.DateTime(timezone=True)),
        sa.Column("capabilities_json", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "filter_sets",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("rules_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column(
            "remote_id",
            sa.String(32),
            sa.ForeignKey("remotes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("local_path", sa.Text(), nullable=False),
        sa.Column("remote_path", sa.Text(), nullable=False),
        sa.Column("direction", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("delete_policy", sa.String(32), nullable=False, server_default="never"),
        sa.Column(
            "quarantine_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("max_deletes", sa.Integer(), server_default="100"),
        sa.Column("max_delete_percent", sa.Integer(), server_default="10"),
        sa.Column(
            "dry_run_required", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "filter_set_id",
            sa.String(32),
            sa.ForeignKey("filter_sets.id", ondelete="SET NULL"),
        ),
        sa.Column("schedule_json", sa.Text()),
        sa.Column("bandwidth_json", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(32), nullable=False, server_default="ready"),
        sa.Column("last_run_id", sa.String(32)),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tasks_next_run_at", "tasks", ["next_run_at"])

    op.create_table(
        "task_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(32),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("transferred_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("transferred_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deleted_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rclone_version", sa.String(64)),
        sa.Column("summary_json", sa.Text()),
    )
    op.create_index(
        "ix_task_runs_task_started", "task_runs", ["task_id", "started_at"]
    )

    op.create_table(
        "task_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.String(32),
            sa.ForeignKey("task_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("path", sa.Text()),
        sa.Column("size", sa.Integer()),
        sa.Column("message", sa.Text()),
    )
    op.create_index("ix_task_events_run_kind", "task_events", ["run_id", "kind"])

    op.create_table(
        "settings",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_index("ix_task_events_run_kind", table_name="task_events")
    op.drop_table("task_events")
    op.drop_index("ix_task_runs_task_started", table_name="task_runs")
    op.drop_table("task_runs")
    op.drop_index("ix_tasks_next_run_at", table_name="tasks")
    op.drop_table("tasks")
    op.drop_table("filter_sets")
    op.drop_table("remotes")
