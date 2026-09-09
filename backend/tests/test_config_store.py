"""Propriété du rclone.conf (SEC-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from csm.rclone.config_store import ConfigEncrypted, RcloneConfigStore


@pytest.fixture
def store(tmp_path: Path) -> RcloneConfigStore:
    return RcloneConfigStore(tmp_path / "rclone.conf")


def test_ensure_exists_creates_file(store: RcloneConfigStore) -> None:
    assert not store.exists()
    store.ensure_exists()
    assert store.exists()
    assert store.sections() == []


def test_upsert_and_read_section(store: RcloneConfigStore) -> None:
    store.upsert_section("monsftp", {"type": "sftp", "host": "192.168.1.10"})
    assert store.sections() == ["monsftp"]
    assert dict(store.read()["monsftp"]) == {"type": "sftp", "host": "192.168.1.10"}


def test_upsert_replaces_entirely(store: RcloneConfigStore) -> None:
    """Une option retirée ne doit pas survivre en douce dans le fichier."""
    store.upsert_section("s", {"type": "sftp", "host": "a", "user": "renald"})
    store.upsert_section("s", {"type": "sftp", "host": "b"})
    assert dict(store.read()["s"]) == {"type": "sftp", "host": "b"}


def test_remove_section(store: RcloneConfigStore) -> None:
    store.upsert_section("s", {"type": "local"})
    assert store.remove_section("s") is True
    assert store.remove_section("s") is False
    assert store.sections() == []


def test_values_with_percent_are_preserved(store: RcloneConfigStore) -> None:
    """L'interpolation configparser casserait un mot de passe contenant « % »."""
    store.upsert_section("s", {"type": "webdav", "pass": "abc%def%%ghi"})
    assert store.read()["s"]["pass"] == "abc%def%%ghi"


def test_no_leftover_temporary_file(store: RcloneConfigStore) -> None:
    store.upsert_section("s", {"type": "local"})
    siblings = list(store.path.parent.iterdir())
    assert [p.name for p in siblings] == ["rclone.conf"]


def test_encrypted_config_is_detected_and_refused(store: RcloneConfigStore) -> None:
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        "# Encrypted rclone configuration File\n\nRCLONE_ENCRYPT_V0:\nZm9vYmFy\n",
        encoding="utf-8",
    )
    assert store.is_encrypted() is True
    with pytest.raises(ConfigEncrypted):
        store.read()
