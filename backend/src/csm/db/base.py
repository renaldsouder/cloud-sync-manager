"""Accès SQLite.

WAL est indispensable ici : l'API doit rester lisible pendant qu'une
exécution écrit ses événements, faute de quoi le tableau de bord se bloque
pendant un transfert long (§13).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def sqlite_url(db_path: Path) -> str:
    """URL SQLAlchemy pour un fichier SQLite, valable aussi sous Windows."""
    return f"sqlite:///{db_path.as_posix()}"


def create_db_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        sqlite_url(db_path),
        future=True,
        # L'API et le superviseur d'exécutions partagent la connexion depuis
        # des threads différents ; l'accès reste sérialisé par SQLite.
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _record) -> None:  # pragma: no cover - trivial
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
