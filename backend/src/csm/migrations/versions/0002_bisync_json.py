"""Réglages du bidirectionnel sur la tâche (SYNC-003).

Une seule colonne JSON plutôt que quatre colonnes : elle porte l'état
d'initialisation — bisync refuse de tourner tant qu'un ``--resync`` explicite
n'a pas établi la référence — et la politique de conflit.

L'autogénération proposait aussi une contrainte d'unicité sur ``settings.key``,
artefact de la réflexion SQLite sur une colonne déjà clé primaire. Elle a été
retirée : elle n'aurait rien ajouté, et son ``downgrade`` sans nom aurait
échoué.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("bisync_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("bisync_json")
