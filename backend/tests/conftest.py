from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.config import Settings
from csm.main import create_app


@pytest.fixture
def local_root(tmp_path: Path) -> Path:
    root = tmp_path / "mnt" / "user"
    root.mkdir(parents=True)
    return root


@pytest.fixture
def settings(tmp_path: Path, local_root: Path) -> Settings:
    return Settings(
        config_dir=tmp_path / "config",
        # Répertoire volontairement absent : on teste l'API, pas le build web.
        web_dir=tmp_path / "no-web",
        allowed_roots=str(local_root),
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
