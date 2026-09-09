"""Modèle de données applicatif (§6).

Les identifiants des remotes, tâches et exécutions sont des UUID hexadécimaux
générés par l'application, pas des entiers auto-incrémentés : ils doivent
rester **stables** à travers un export/import de configuration et un
changement de serveur (GLOBAL-007, DATA-002, DATA-003).

Aucun secret Cloud n'est stocké ici : ils vivent dans le ``rclone.conf``
chiffré de l'appdata (§6.1, SEC-001).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator):
    """Date toujours écrite et relue en UTC.

    SQLite ne conserve pas le fuseau : une date écrite en heure locale
    revient naïve et serait interprétée comme de l'UTC, décalée du décalage
    horaire. Le piège est silencieux et fausserait toutes les échéances de
    planification, alors on le neutralise ici, une fois, pour toutes les
    colonnes plutôt que de compter sur la vigilance à chaque écriture.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):  # type: ignore[override]
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):  # type: ignore[override]
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), default=utcnow, onupdate=utcnow
    )


class Remote(TimestampMixin, Base):
    """Un stockage distant (CLOUD-001)."""

    __tablename__ = "remotes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    provider: Mapped[str] = mapped_column(String(64))
    #: Nom de la section dans le rclone.conf. Séparé de ``name`` pour que
    #: renommer un stockage dans l'UI ne casse pas les tâches existantes.
    rclone_remote_name: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="unknown")
    last_test_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    #: Matrice de capacités réellement constatées (§9.3). Une connexion réussie
    #: ne signifie pas que toutes les opérations sont supportées.
    capabilities_json: Mapped[str | None] = mapped_column(Text)

    tasks: Mapped[list["Task"]] = relationship(back_populates="remote")


class FilterSet(TimestampMixin, Base):
    """Jeu de règles d'inclusion/exclusion réutilisable (FILT-001, FILT-002)."""

    __tablename__ = "filter_sets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    rules_json: Mapped[str] = mapped_column(Text, default="[]")


class Task(TimestampMixin, Base):
    """Une synchronisation (TASK-001)."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    remote_id: Mapped[str] = mapped_column(ForeignKey("remotes.id", ondelete="RESTRICT"))

    local_path: Mapped[str] = mapped_column(Text)
    remote_path: Mapped[str] = mapped_column(Text)

    #: ``local_to_remote`` | ``remote_to_local``
    direction: Mapped[str] = mapped_column(String(32))
    #: ``copy`` | ``mirror`` | ``bisync`` (§7)
    mode: Mapped[str] = mapped_column(String(16))

    #: Politique de suppression. Non destructive par défaut : la propagation
    #: exige un choix explicite de l'utilisateur (CONF-001, CONF-002).
    delete_policy: Mapped[str] = mapped_column(String(32), default="never")
    #: Quarantaine plutôt que suppression réelle côté local (CONF-004, P13).
    #: Activée par défaut, c'est ce qui rend un Miroir descendant réversible.
    quarantine_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Garde-fous du §8.3 : au-delà, la tâche passe « Bloquée ».
    max_deletes: Mapped[int | None] = mapped_column(Integer, default=100)
    max_delete_percent: Mapped[int | None] = mapped_column(Integer, default=10)

    #: Une simulation est exigée avant la première exécution destructive et
    #: après tout changement de source, destination ou sens (§8.1, SYNC-004).
    dry_run_required: Mapped[bool] = mapped_column(Boolean, default=True)

    filter_set_id: Mapped[str | None] = mapped_column(
        ForeignKey("filter_sets.id", ondelete="SET NULL")
    )
    schedule_json: Mapped[str | None] = mapped_column(Text)
    bandwidth_json: Mapped[str | None] = mapped_column(Text)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: État courant : Prête, Planifiée, En cours, En pause, Réussie,
    #: Avertissement, Erreur, Bloquée (§10.2).
    status: Mapped[str] = mapped_column(String(32), default="ready")
    last_run_id: Mapped[str | None] = mapped_column(String(32))
    next_run_at: Mapped[datetime | None] = mapped_column(UtcDateTime())

    remote: Mapped[Remote] = relationship(back_populates="tasks")
    runs: Mapped[list["TaskRun"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_tasks_next_run_at", "next_run_at"),)


class TaskRun(Base):
    """Une exécution (LOG-001)."""

    __tablename__ = "task_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))

    started_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    #: ``running`` | ``success`` | ``warning`` | ``error`` | ``blocked``
    #: | ``interrupted``. Après un arrêt forcé ou un crash, jamais ``success`` (§8.5).
    status: Mapped[str] = mapped_column(String(32), default="running")
    exit_code: Mapped[int | None] = mapped_column(Integer)

    transferred_files: Mapped[int] = mapped_column(Integer, default=0)
    transferred_bytes: Mapped[int] = mapped_column(Integer, default=0)
    deleted_files: Mapped[int] = mapped_column(Integer, default=0)
    errors_count: Mapped[int] = mapped_column(Integer, default=0)

    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    rclone_version: Mapped[str | None] = mapped_column(String(64))
    summary_json: Mapped[str | None] = mapped_column(Text)

    task: Mapped[Task] = relationship(back_populates="runs")
    events: Mapped[list["TaskEvent"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_task_runs_task_started", "task_id", "started_at"),)


class TaskEvent(Base):
    """Événement structuré d'une exécution (§8.4, LOG-002).

    Toute suppression laisse une trace ici. C'est la source de l'écran de
    confirmation « Supprimer 423 fichiers » et de l'historique destructif.
    """

    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("task_runs.id", ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    #: ``transfer`` | ``delete`` | ``error`` | ``retry`` | ``conflict`` | ``warning``
    kind: Mapped[str] = mapped_column(String(32))
    path: Mapped[str | None] = mapped_column(Text)
    size: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str | None] = mapped_column(Text)

    run: Mapped[TaskRun] = relationship(back_populates="events")

    __table_args__ = (Index("ix_task_events_run_kind", "run_id", "kind"),)


class Setting(Base):
    """Paramètres généraux, rétention, UI, notifications (§6)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (UniqueConstraint("key"),)
