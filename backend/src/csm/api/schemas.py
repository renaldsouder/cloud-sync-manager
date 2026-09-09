"""Schémas d'API.

Aucun schéma de sortie ne comporte de champ secret : un jeton enregistré
n'est jamais renvoyé, même masqué partiellement (§6.1, SEC-001).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from csm.db.models import Remote


class ProviderOption(BaseModel):
    name: str
    help: str
    help_full: str
    type: str
    required: bool
    advanced: bool
    is_password: bool
    sensitive: bool
    default: str | None = None
    examples: list[dict[str, Any]] = Field(default_factory=list)


class ProviderOut(BaseModel):
    name: str
    label: str
    description: str
    priority: bool
    needs_oauth: bool
    options: list[ProviderOption]


class RemoteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=64)
    options: dict[str, str] = Field(default_factory=dict)


class RemoteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    options: dict[str, str] | None = None


class RemoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    provider: str
    rclone_remote_name: str
    status: str
    last_test_at: datetime | None = None
    capabilities: dict[str, Any] | None = None
    task_count: int = 0
    options: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def build(
        cls,
        remote: Remote,
        *,
        options: dict[str, str] | None = None,
        task_count: int = 0,
    ) -> "RemoteOut":
        capabilities: dict[str, Any] | None = None
        if remote.capabilities_json:
            try:
                capabilities = json.loads(remote.capabilities_json)
            except ValueError:
                capabilities = None
        return cls(
            id=remote.id,
            name=remote.name,
            provider=remote.provider,
            rclone_remote_name=remote.rclone_remote_name,
            status=remote.status,
            last_test_at=remote.last_test_at,
            capabilities=capabilities,
            task_count=task_count,
            options=options or {},
        )


class RemoteTestOut(BaseModel):
    ok: bool
    detail: str
    elapsed_ms: int
    capabilities: dict[str, Any]
    entries: list[str]
