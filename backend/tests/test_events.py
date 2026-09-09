"""Lecture du journal JSON de rclone.

Les lignes ci-dessous sont des captures réelles de rclone v1.75.1, pas des
approximations : c'est le format qui compte, et il n'a d'intérêt que s'il
correspond à ce que le moteur produit vraiment.
"""

from __future__ import annotations

from csm.rclone.events import (
    DELETE,
    ERROR,
    SKIP_DELETE,
    SKIP_TRANSFER,
    STATS,
    TRANSFER,
    parse_line,
    summarise,
)

COPIED = (
    '{"time":"2026-09-09T13:55:22.011Z","level":"info","msg":"Copied (new)",'
    '"size":2,"object":"sous/b éà.txt","objectType":"*local.Object",'
    '"source":"operations/copy.go:380"}'
)
SKIPPED_COPY = (
    '{"time":"2026-09-09T13:55:34.191Z","level":"notice",'
    '"msg":"Skipped copy as --dry-run is set (size 1)","skipped":"copy",'
    '"size":1,"object":"nouveau.txt"}'
)
SKIPPED_DELETE = (
    '{"time":"2026-09-09T13:55:34.191Z","level":"notice",'
    '"msg":"Skipped delete as --dry-run is set (size 1)","skipped":"delete",'
    '"size":1,"object":"aeffacer.txt"}'
)
DELETED = (
    '{"time":"2026-09-09T13:55:34.191Z","level":"info","msg":"Deleted",'
    '"size":1,"object":"vieux.txt"}'
)
FAILED = (
    '{"time":"2026-09-09T13:55:34.191Z","level":"error",'
    '"msg":"Failed to copy: permission denied","object":"secret.txt"}'
)
STATS_LINE = (
    '{"time":"2026-09-09T13:55:34.191Z","level":"notice","msg":"Transferred...",'
    '"stats":{"bytes":6,"checks":1,"deletes":1,"elapsedTime":0.0026,"errors":0,'
    '"eta":null,"speed":123.5,"totalBytes":6,"totalTransfers":2,"transfers":2,'
    '"fatalError":false}}'
)


def test_copied_is_a_transfer() -> None:
    event = parse_line(COPIED)
    assert event is not None
    assert event.kind == TRANSFER
    assert event.path == "sous/b éà.txt"
    assert event.size == 2


def test_dry_run_names_the_files_it_would_touch() -> None:
    """§8.3 — c'est la simulation qui rend le seuil de suppression possible."""
    copy_event = parse_line(SKIPPED_COPY)
    delete_event = parse_line(SKIPPED_DELETE)
    assert copy_event is not None and copy_event.kind == SKIP_TRANSFER
    assert delete_event is not None and delete_event.kind == SKIP_DELETE
    assert delete_event.path == "aeffacer.txt"


def test_real_delete_is_recognised() -> None:
    event = parse_line(DELETED)
    assert event is not None
    assert event.kind == DELETE
    assert event.path == "vieux.txt"


def test_error_level_wins() -> None:
    event = parse_line(FAILED)
    assert event is not None
    assert event.kind == ERROR
    assert "permission denied" in event.message


def test_stats_are_summarised() -> None:
    event = parse_line(STATS_LINE)
    assert event is not None
    assert event.kind == STATS
    assert event.stats is not None

    summary = summarise(event.stats)
    assert summary["bytes"] == 6
    assert summary["deletes"] == 1
    assert summary["transfers"] == 2
    assert summary["speed"] == 123.5


def test_non_json_lines_are_ignored() -> None:
    assert parse_line("") is None
    assert parse_line("\n") is None
    assert parse_line("2026/09/09 NOTICE: texte libre") is None
    assert parse_line("{pas du json}") is None
