"""Notes de version (UPDATE-004)."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from csm import __version__
from csm.release_notes import RELEASES


def test_the_running_version_always_has_its_notes() -> None:
    """Sans ce test, on publierait une version dont l'interface ne sait
    rien dire — et l'utilisateur qui met à jour ne verrait pas ce qui a
    changé dans la protection de ses données."""
    assert __version__ in {release["version"] for release in RELEASES}


def test_releases_are_well_formed_and_newest_first() -> None:
    published = [release for release in RELEASES if release["version"]]
    unreleased = [release for release in RELEASES if not release["version"]]
    assert len(unreleased) <= 1
    if unreleased:
        assert RELEASES[0] is unreleased[0], "le bloc à paraître vient en tête"

    def key(version: str) -> tuple[int, ...]:
        return tuple(int(part) for part in version.split("."))

    versions = [release["version"] for release in published]
    assert versions == sorted(versions, key=key, reverse=True)
    for release in published:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", release["date"])
        assert release["important"] or release["highlights"]


def test_the_notes_are_served(client: TestClient) -> None:
    payload = client.get("/api/release-notes").json()
    assert payload["current"] == __version__
    assert payload["releases"][0] == RELEASES[0]
