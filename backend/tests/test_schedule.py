"""Règles de planification (§11) — fonctions pures, horloge fournie.

C'est l'argument qui avait fait préférer un planificateur maison à
APScheduler : la politique de rattrapage doit se démontrer, pas se déduire
du réglage d'une bibliothèque.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest

from csm.services.schedule import (
    ONCE,
    SKIP,
    Schedule,
    ScheduleError,
    describe,
    next_occurrence,
    resolve_after_restart,
)

# Mercredi 9 septembre 2026, 14:00.
NOW = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)


def test_manual_has_no_deadline() -> None:
    schedule = Schedule.parse(None)
    assert schedule.automatic is False
    assert next_occurrence(schedule, NOW) is None
    assert schedule.to_json() is None


def test_interval() -> None:
    schedule = Schedule.from_dict({"kind": "interval", "minutes": 30})
    assert next_occurrence(schedule, NOW) == NOW + timedelta(minutes=30)


def test_daily_today_then_tomorrow() -> None:
    schedule = Schedule.from_dict({"kind": "daily", "time": "22:30"})
    assert next_occurrence(schedule, NOW) == NOW.replace(hour=22, minute=30)

    late = NOW.replace(hour=23)
    assert next_occurrence(schedule, late) == (late + timedelta(days=1)).replace(
        hour=22, minute=30
    )


def test_daily_at_the_exact_minute_goes_to_the_next_day() -> None:
    """Sinon la tâche se relancerait en boucle pendant toute la minute."""
    schedule = Schedule.from_dict({"kind": "daily", "time": "14:00"})
    assert next_occurrence(schedule, NOW).day == NOW.day + 1


def test_weekly_selects_the_next_matching_day() -> None:
    # Lundi et vendredi ; on est mercredi.
    schedule = Schedule.from_dict({"kind": "weekly", "days": [1, 5], "time": "02:30"})
    following = next_occurrence(schedule, NOW)
    assert following.isoweekday() == 5
    assert (following.day, following.hour, following.minute) == (11, 2, 30)


def test_weekly_wraps_to_the_following_week() -> None:
    schedule = Schedule.from_dict({"kind": "weekly", "days": [2], "time": "02:30"})
    following = next_occurrence(schedule, NOW)
    assert following.isoweekday() == 2
    assert following.day == 15


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "cron"},
        {"kind": "interval"},
        {"kind": "interval", "minutes": 0},
        {"kind": "daily"},
        {"kind": "daily", "time": "25:00"},
        {"kind": "weekly", "time": "02:30"},
        {"kind": "weekly", "time": "02:30", "days": [9]},
        {"kind": "daily", "time": "02:30", "catch_up": "toutes"},
    ],
)
def test_invalid_schedules_are_refused(payload: dict) -> None:
    with pytest.raises(ScheduleError):
        Schedule.from_dict(payload)


def test_roundtrip() -> None:
    original = Schedule.from_dict(
        {"kind": "weekly", "days": [5, 1], "time": "02:30", "catch_up": ONCE}
    )
    assert Schedule.parse(original.to_json()) == original
    assert original.days == (1, 5)
    assert original.at == time(2, 30)


# -- politique de rattrapage (§11) ------------------------------------------


def test_a_future_deadline_is_kept_as_is() -> None:
    schedule = Schedule.from_dict({"kind": "interval", "minutes": 60})
    stored = NOW + timedelta(minutes=10)
    assert resolve_after_restart(schedule, NOW, stored) == (stored, False)


def test_missed_deadline_is_skipped_by_default() -> None:
    """Le conteneur a été arrêté six heures : on ne rejoue pas les six
    occurrences manquées, ce que le §11 interdit explicitement."""
    schedule = Schedule.from_dict({"kind": "interval", "minutes": 60})
    missed = NOW - timedelta(hours=6)

    resolved, catch_up = resolve_after_restart(schedule, NOW, missed)

    assert catch_up is False
    assert resolved == NOW + timedelta(minutes=60)
    assert resolved > NOW, "aucune exécution ne doit partir au démarrage"


def test_missed_deadline_is_replayed_once_when_asked() -> None:
    schedule = Schedule.from_dict({"kind": "interval", "minutes": 60, "catch_up": ONCE})
    missed = NOW - timedelta(hours=6)

    resolved, catch_up = resolve_after_restart(schedule, NOW, missed)

    assert catch_up is True
    assert resolved == missed, "une seule occurrence, pas la file entière"


def test_first_start_computes_a_deadline() -> None:
    schedule = Schedule.from_dict({"kind": "daily", "time": "02:30", "catch_up": SKIP})
    resolved, catch_up = resolve_after_restart(schedule, NOW, None)
    assert catch_up is False
    assert resolved == (NOW + timedelta(days=1)).replace(hour=2, minute=30)


def test_manual_tasks_have_nothing_to_resolve() -> None:
    assert resolve_after_restart(Schedule(), NOW, NOW - timedelta(days=1)) == (None, False)


# -- formulation -------------------------------------------------------------


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({}, "manuelle"),
        ({"kind": "interval", "minutes": 15}, "toutes les 15 minutes"),
        ({"kind": "interval", "minutes": 60}, "toutes les 1 heure"),
        ({"kind": "interval", "minutes": 180}, "toutes les 3 heures"),
        ({"kind": "daily", "time": "02:30"}, "tous les jours à 02:30"),
        (
            {"kind": "weekly", "days": [1, 3], "time": "22:00"},
            "lundi, mercredi à 22:00",
        ),
    ],
)
def test_human_readable(payload: dict, expected: str) -> None:
    assert describe(Schedule.from_dict(payload)) == expected
