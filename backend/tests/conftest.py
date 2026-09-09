from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.config import Settings
from csm.main import create_app

#: En développement, le binaire rclone est déposé dans ``backend/.tools/``
#: (ignoré par git) ; en production, l'image le fournit dans le PATH.
TOOLS_DIR = Path(__file__).resolve().parents[1] / ".tools"


def discover_rclone() -> str | None:
    configured = os.environ.get("CSM_RCLONE_BINARY")
    if configured and Path(configured).is_file():
        return configured
    for name in ("rclone.exe", "rclone"):
        candidate = TOOLS_DIR / name
        if candidate.is_file():
            return str(candidate)
    return shutil.which("rclone")


@pytest.fixture(scope="session")
def rclone_path() -> str:
    found = discover_rclone()
    if not found:
        pytest.skip(
            "binaire rclone introuvable — déposez-le dans backend/.tools/ "
            "ou renseignez CSM_RCLONE_BINARY"
        )
    return found


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
        rclone_binary=discover_rclone(),
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
