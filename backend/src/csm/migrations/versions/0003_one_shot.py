"""Copie ponctuelle (SYNC-008).

Une copie ponctuelle est une tâche marquée ``one_shot`` : elle réutilise
tout le moteur — validation des chemins, exécution, historique fichier par
fichier — sans apparaître parmi les tâches permanentes ni pouvoir être
planifiée. Une colonne booléenne plutôt qu'une table à part : une seconde
table aurait demandé de dupliquer ``task_runs`` et ses garde-fous.

``server_default`` à 0 : les tâches existantes restent des tâches
permanentes, sans exception.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "one_shot",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("one_shot")
