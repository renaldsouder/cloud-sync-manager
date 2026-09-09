"""Pilotage programmatique d'Alembic.

Les migrations sont appliquées **au démarrage du conteneur**, pas par une
commande manuelle : une mise à jour d'image ne doit jamais laisser un appdata
dans un état intermédiaire (DATA-004, UPDATE-002).
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def alembic_config(db_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", db_url)
    return config


def upgrade_to_head(db_url: str) -> None:
    command.upgrade(alembic_config(db_url), "head")


def downgrade_to(db_url: str, revision: str) -> None:
    command.downgrade(alembic_config(db_url), revision)
