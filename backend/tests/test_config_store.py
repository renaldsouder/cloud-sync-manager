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


def test_a_transient_lock_does_not_lose_the_configuration(
    store: RcloneConfigStore, monkeypatch
) -> None:
    """Sous Windows, la substitution du fichier échoue par intermittence.

    Observé une fois sur neuf sur la suite : un antivirus tient brièvement
    le fichier fraîchement écrit et `os.replace` rend ERROR_ACCESS_DENIED.
    Perdre l'enregistrement d'un stockage pour cette raison serait absurde.
    """
    import os as os_module

    original = os_module.replace
    refusals = {"left": 2}

    def flaky(source, destination):
        if refusals["left"]:
            refusals["left"] -= 1
            raise PermissionError(5, "Accès refusé")
        return original(source, destination)

    monkeypatch.setattr(os_module, "replace", flaky)
    store.upsert_section("monsftp", {"type": "sftp", "host": "192.168.1.10"})

    assert refusals["left"] == 0, "les deux refus auraient dû être absorbés"
    assert dict(store.read()["monsftp"])["host"] == "192.168.1.10"


def test_a_persistent_lock_is_still_reported(
    store: RcloneConfigStore, monkeypatch
) -> None:
    """Réessayer, oui ; masquer une panne durable, non."""
    import os as os_module

    def always_refuse(source, destination):
        raise PermissionError(5, "Accès refusé")

    monkeypatch.setattr(os_module, "replace", always_refuse)
    with pytest.raises(PermissionError):
        store.upsert_section("monsftp", {"type": "sftp"})
