"""Cycle de vie d'un stockage distant (CLOUD-001, CLOUD-006, SEC-001)."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from csm.db.models import Remote, Task
from csm.rclone.adapter import RcloneAdapter, RcloneError
from csm.rclone.config_store import RcloneConfigStore
from csm.rclone import providers as provider_catalog

#: Jeu de caractères accepté par rclone pour un nom de section.
REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")

#: Valeur renvoyée à l'UI à la place d'un secret enregistré (§6.1).
CONFIGURED = "configuré"


class RemoteError(RuntimeError):
    """Erreur fonctionnelle sur un stockage, message destiné à l'utilisateur."""


def slugify_remote_name(label: str) -> str:
    """Nom de section rclone dérivé du libellé utilisateur.

    Minimum deux caractères : sous Windows, ``u:`` désigne le lecteur U:
    avant de désigner un remote nommé « u ». Unraid n'a pas de lettres de
    lecteur, mais un nom ambigu n'apporte rien et compliquerait le support.
    """
    cleaned = re.sub(r"[^A-Za-z0-9_.\-]+", "-", label.strip()).strip("-.")
    if len(cleaned) < 2:
        cleaned = f"remote-{cleaned}" if cleaned else "remote"
    return cleaned[:64]


def _provider_spec(adapter: RcloneAdapter, provider: str) -> dict[str, Any]:
    for candidate in adapter.providers():
        if candidate.get("Name") == provider:
            return candidate
    raise RemoteError(f"fournisseur inconnu : {provider}")


def create_remote(
    session: Session,
    adapter: RcloneAdapter,
    store: RcloneConfigStore,
    *,
    name: str,
    provider: str,
    options: dict[str, str],
) -> Remote:
    """Crée un stockage : section rclone + métadonnées.

    Les mots de passe sont obscurcis via ``stdin`` puis écrits directement
    dans le fichier INI — ils ne passent jamais en ligne de commande.
    """
    if session.scalar(select(Remote).where(Remote.name == name)):
        raise RemoteError(f"un stockage nommé « {name} » existe déjà")

    spec = _provider_spec(adapter, provider)
    to_obscure = provider_catalog.password_fields(spec)

    rclone_name = _unique_rclone_name(session, store, slugify_remote_name(name))

    section: dict[str, str] = {"type": provider}
    for key, value in options.items():
        if value is None or value == "":
            continue
        section[key] = adapter.obscure(value) if key in to_obscure else str(value)

    store.ensure_exists()
    store.upsert_section(rclone_name, section)

    remote = Remote(
        name=name,
        provider=provider,
        rclone_remote_name=rclone_name,
        status="unknown",
    )
    session.add(remote)
    session.flush()
    return remote


def _unique_rclone_name(
    session: Session, store: RcloneConfigStore, base: str
) -> str:
    existing = set(store.sections()) if store.exists() else set()
    existing |= {
        row for row in session.scalars(select(Remote.rclone_remote_name)).all()
    }
    if base not in existing:
        return base
    for suffix in range(2, 1000):
        candidate = f"{base}-{suffix}"
        if candidate not in existing:
            return candidate
    raise RemoteError("impossible de générer un nom de section unique")


def update_remote(
    session: Session,
    adapter: RcloneAdapter,
    store: RcloneConfigStore,
    remote: Remote,
    *,
    name: str | None = None,
    options: dict[str, str] | None = None,
) -> Remote:
    """Renomme un stockage et/ou remplace ses options.

    Le nom de section rclone n'est **pas** modifié : le renommer casserait
    les tâches qui le référencent.
    """
    if name and name != remote.name:
        if session.scalar(select(Remote).where(Remote.name == name, Remote.id != remote.id)):
            raise RemoteError(f"un stockage nommé « {name} » existe déjà")
        remote.name = name

    if options is not None:
        spec = _provider_spec(adapter, remote.provider)
        to_obscure = provider_catalog.password_fields(spec)
        current = dict(store.read()[remote.rclone_remote_name]) if store.has_section(
            remote.rclone_remote_name
        ) else {}

        section: dict[str, str] = {"type": remote.provider}
        for key, value in options.items():
            if value == CONFIGURED:
                # L'UI a renvoyé le marqueur : on conserve la valeur en place
                # plutôt que d'écrire « configuré » comme mot de passe.
                if key in current:
                    section[key] = current[key]
                continue
            if value is None or value == "":
                continue
            section[key] = adapter.obscure(value) if key in to_obscure else str(value)

        store.upsert_section(remote.rclone_remote_name, section)
        remote.status = "unknown"

    session.flush()
    return remote


def delete_remote(
    session: Session, store: RcloneConfigStore, remote: Remote
) -> None:
    linked = session.scalar(
        select(Task).where(Task.remote_id == remote.id).limit(1)
    )
    if linked is not None:
        raise RemoteError(
            f"« {remote.name} » est utilisé par la tâche « {linked.name} » : "
            "supprimez d'abord la tâche"
        )
    store.remove_section(remote.rclone_remote_name)
    session.delete(remote)
    session.flush()


def test_remote(
    session: Session, adapter: RcloneAdapter, remote: Remote, *, timeout: float = 30.0
) -> dict[str, Any]:
    """Vérifie l'accès et relève les capacités constatées (§9.3).

    Une connexion réussie ne prouve pas que tout est supporté : on note
    séparément ce qui a réellement répondu.
    """
    started = time.perf_counter()
    capabilities: dict[str, Any] = {"list": False, "about": False}
    try:
        entries = adapter.lsjson(
            f"{remote.rclone_remote_name}:", dirs_only=True, timeout=timeout
        )
        capabilities["list"] = True
        detail = f"{len(entries)} dossier(s) à la racine"
        ok = True
    except RcloneError as exc:
        entries = []
        detail = str(exc)
        ok = False

    if ok:
        about = adapter.about(remote.rclone_remote_name + ":", timeout=timeout)
        capabilities["about"] = about is not None
        if about:
            capabilities["quota"] = about

    remote.status = "ok" if ok else "error"
    remote.last_test_at = datetime.now(timezone.utc)
    remote.capabilities_json = json.dumps(capabilities, ensure_ascii=False)
    session.flush()

    return {
        "ok": ok,
        "detail": detail,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "capabilities": capabilities,
        "entries": [entry.get("Name") for entry in entries][:50],
    }


def complete_onedrive(
    session: Session,
    adapter: RcloneAdapter,
    store: RcloneConfigStore,
    remote: Remote,
) -> dict[str, str]:
    """Renseigne ``drive_id`` et ``drive_type`` d'un OneDrive incomplet.

    L'autorisation peut aboutir sans que Microsoft ait livré l'identifiant
    du disque. Le jeton est alors valide mais le stockage inutilisable :
    plutôt que d'imposer de tout recommencer, on réinterroge Graph avec le
    jeton déjà enregistré.
    """
    from csm.services import oauth

    if remote.provider != "onedrive":
        raise RemoteError("cette réparation ne concerne que OneDrive")
    if not store.has_section(remote.rclone_remote_name):
        raise RemoteError("configuration introuvable pour ce stockage")

    section = dict(store.read()[remote.rclone_remote_name])
    blob = section.get("token", "")
    if not blob:
        raise RemoteError(
            "aucun jeton enregistré : relancez l'autorisation depuis l'assistant"
        )

    try:
        found = oauth.describe_drive(oauth.access_token_of(blob))
    except oauth.OAuthError as exc:
        raise RemoteError(str(exc)) from exc

    section.update(found)
    store.upsert_section(remote.rclone_remote_name, section)
    remote.status = "unknown"
    session.flush()
    return found


def describe_options(
    adapter: RcloneAdapter, store: RcloneConfigStore, remote: Remote
) -> dict[str, str]:
    """Options du stockage, secrets remplacés par « configuré » (§6.1)."""
    if not store.has_section(remote.rclone_remote_name):
        return {}
    section = dict(store.read()[remote.rclone_remote_name])
    try:
        secrets = provider_catalog.hidden_fields(
            _provider_spec(adapter, remote.provider)
        )
    except RemoteError:
        secrets = set()
    return {
        key: (CONFIGURED if key in secrets and value else value)
        for key, value in section.items()
        if key != "type"
    }
