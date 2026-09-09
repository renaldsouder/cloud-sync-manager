"""Règles d'exécution vérifiables sans lancer rclone."""

from __future__ import annotations

from types import SimpleNamespace

from csm.rclone.adapter import RcloneAdapter
from csm.services.runner import _final_status, endpoints, requires_dry_run


def _live(*, cancelled: bool, errors: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        cancelled=cancelled, counters={"errors": errors}, stats={"errors": 0}
    )


def test_cancelled_run_is_never_successful() -> None:
    """§8.5 — après un arrêt forcé, jamais « Réussie », même si rclone sort à 0."""
    assert _final_status(_live(cancelled=True), 0) == "interrupted"
    assert _final_status(_live(cancelled=True), 143) == "interrupted"


def test_status_mapping() -> None:
    assert _final_status(_live(cancelled=False), 0) == "success"
    assert _final_status(_live(cancelled=False), 1) == "error"
    assert _final_status(_live(cancelled=False, errors=3), 0) == "warning"


def test_endpoints_follow_the_direction() -> None:
    task = SimpleNamespace(
        local_path="/mnt/user/Photos",
        remote_path="Sauvegardes/Photos",
        direction="local_to_remote",
    )
    remote = SimpleNamespace(rclone_remote_name="gdrive")

    assert endpoints(task, remote) == (
        "/mnt/user/Photos",
        "gdrive:Sauvegardes/Photos",
    )

    task.direction = "remote_to_local"
    assert endpoints(task, remote) == (
        "gdrive:Sauvegardes/Photos",
        "/mnt/user/Photos",
    )


def test_copy_does_not_require_a_simulation() -> None:
    """Une Copie ne supprime rien : imposer la simulation banaliserait le
    garde-fou là où il compte (§8.1)."""
    assert requires_dry_run(SimpleNamespace(dry_run_required=True, mode="copy")) is False
    assert requires_dry_run(SimpleNamespace(dry_run_required=True, mode="mirror")) is True
    assert requires_dry_run(SimpleNamespace(dry_run_required=False, mode="mirror")) is False


def test_dry_run_differs_only_by_one_flag(tmp_path) -> None:
    """§20.3 — la simulation doit emprunter exactement le même chemin que
    l'exécution réelle, sans quoi elle ne prouve rien."""
    adapter = RcloneAdapter(binary="rclone", config_path=tmp_path / "rclone.conf")
    real = adapter.build_transfer_args("copy", "/src", "dst:chemin")
    simulated = adapter.build_transfer_args("copy", "/src", "dst:chemin", dry_run=True)

    assert simulated == real + ["--dry-run"]
    assert "--use-json-log" in real
    assert real[:3] == ["copy", "/src", "dst:chemin"]


def test_unknown_operation_is_refused(tmp_path) -> None:
    adapter = RcloneAdapter(binary="rclone", config_path=tmp_path / "rclone.conf")
    for forbidden in ("delete", "purge", "bisync"):
        try:
            adapter.build_transfer_args(forbidden, "a", "b")
        except ValueError:
            continue
        raise AssertionError(f"{forbidden} aurait dû être refusé")
