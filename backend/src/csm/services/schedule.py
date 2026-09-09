"""Règles de planification (PLAN-001 → PLAN-003, §11).

Le §11 est explicite : le parcours standard ne doit pas demander d'écrire une
expression cron. On décrit donc une planification par ce que l'utilisateur a
en tête — toutes les N minutes, tous les jours à telle heure, certains jours
de la semaine — et l'expression cron reste une option de Roadmap.

Tout ce module est constitué de **fonctions pures** prenant l'heure en
paramètre. C'était l'argument retenu contre APScheduler (P7) : la vraie
difficulté du §11 n'est pas le déclenchement, c'est la politique de
rattrapage, et elle doit être démontrable au banc d'essai plutôt que
déduite du réglage d'une bibliothèque.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta

MANUAL = "manual"
INTERVAL = "interval"
DAILY = "daily"
WEEKLY = "weekly"
KINDS = frozenset({MANUAL, INTERVAL, DAILY, WEEKLY})

#: Politique de rattrapage (§11). ``skip`` est le défaut : au redémarrage,
#: une occurrence manquée n'est pas rejouée, on repart de la prochaine. Le
#: cahier des charges demande que cette politique soit explicite plutôt que
#: subie — ``once`` rejoue une seule fois, jamais la file entière.
SKIP = "skip"
ONCE = "once"


class ScheduleError(ValueError):
    """Planification invalide, message destiné à l'utilisateur."""


@dataclass(frozen=True)
class Schedule:
    kind: str = MANUAL
    minutes: int | None = None
    at: time | None = None
    #: Jours ISO : 1 = lundi … 7 = dimanche.
    days: tuple[int, ...] = ()
    catch_up: str = SKIP

    @property
    def automatic(self) -> bool:
        return self.kind != MANUAL

    # -- sérialisation ------------------------------------------------------

    @classmethod
    def parse(cls, raw: str | None) -> "Schedule":
        if not raw:
            return cls()
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise ScheduleError("planification illisible") from exc
        return cls.from_dict(payload if isinstance(payload, dict) else {})

    @classmethod
    def from_dict(cls, payload: dict) -> "Schedule":
        kind = str(payload.get("kind", MANUAL))
        if kind not in KINDS:
            raise ScheduleError(f"type de planification inconnu : {kind}")

        catch_up = str(payload.get("catch_up", SKIP))
        if catch_up not in {SKIP, ONCE}:
            raise ScheduleError(f"politique de rattrapage inconnue : {catch_up}")

        minutes = payload.get("minutes")
        if kind == INTERVAL:
            if not isinstance(minutes, int) or minutes < 1:
                raise ScheduleError("l'intervalle doit être d'au moins une minute")
            minutes = int(minutes)
        else:
            minutes = None

        at = _parse_time(payload.get("time")) if kind in {DAILY, WEEKLY} else None
        if kind in {DAILY, WEEKLY} and at is None:
            raise ScheduleError("une heure de déclenchement est nécessaire")

        days: tuple[int, ...] = ()
        if kind == WEEKLY:
            raw_days = payload.get("days") or []
            if not isinstance(raw_days, list) or not raw_days:
                raise ScheduleError("sélectionnez au moins un jour de la semaine")
            days = tuple(sorted({_parse_day(day) for day in raw_days}))

        return cls(kind=kind, minutes=minutes, at=at, days=days, catch_up=catch_up)

    def to_dict(self) -> dict:
        payload: dict = {"kind": self.kind, "catch_up": self.catch_up}
        if self.minutes is not None:
            payload["minutes"] = self.minutes
        if self.at is not None:
            payload["time"] = self.at.strftime("%H:%M")
        if self.days:
            payload["days"] = list(self.days)
        return payload

    def to_json(self) -> str | None:
        return None if self.kind == MANUAL else json.dumps(self.to_dict())


def _parse_time(value: object) -> time | None:
    if not isinstance(value, str):
        return None
    try:
        hour, minute = value.split(":", 1)
        return time(int(hour), int(minute))
    except (ValueError, TypeError) as exc:
        raise ScheduleError(f"heure invalide : {value!r}") from exc


def _parse_day(value: object) -> int:
    try:
        day = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ScheduleError(f"jour invalide : {value!r}") from exc
    if not 1 <= day <= 7:
        raise ScheduleError("les jours vont de 1 (lundi) à 7 (dimanche)")
    return day


# -- calcul des occurrences --------------------------------------------------


def next_occurrence(schedule: Schedule, now: datetime, after: datetime | None = None) -> datetime | None:
    """Première exécution **strictement après** ``after`` (par défaut ``now``).

    Renvoie ``None`` pour une tâche manuelle : c'est l'absence d'échéance,
    pas une erreur.
    """
    if not schedule.automatic:
        return None

    reference = after or now

    if schedule.kind == INTERVAL:
        assert schedule.minutes is not None
        return reference + timedelta(minutes=schedule.minutes)

    assert schedule.at is not None
    candidate = reference.replace(
        hour=schedule.at.hour, minute=schedule.at.minute, second=0, microsecond=0
    )
    if candidate <= reference:
        candidate += timedelta(days=1)

    if schedule.kind == DAILY:
        return candidate

    # Hebdomadaire : on avance jour par jour jusqu'au premier jour retenu.
    for _ in range(8):
        if candidate.isoweekday() in schedule.days:
            return candidate
        candidate += timedelta(days=1)
    return None  # pragma: no cover - impossible, days n'est jamais vide


def resolve_after_restart(
    schedule: Schedule, now: datetime, stored_next: datetime | None
) -> tuple[datetime | None, bool]:
    """Échéance à retenir au démarrage, et faut-il rattraper (§11).

    Le conteneur peut avoir été arrêté des heures. Rejouer toutes les
    occurrences manquées lancerait plusieurs exécutions concurrentes de la
    même tâche — ce que le §11 interdit explicitement. Par défaut on repart
    donc de la prochaine échéance ; ``once`` rejoue une fois, et une seule.
    """
    if not schedule.automatic:
        return None, False

    if stored_next is None:
        return next_occurrence(schedule, now), False

    if stored_next > now:
        return stored_next, False

    if schedule.catch_up == ONCE:
        # Conserver une échéance dans le passé fait partir la tâche au
        # premier battement, une seule fois : la suivante est recalculée
        # normalement à la fin de l'exécution.
        return stored_next, True

    return next_occurrence(schedule, now), False


def describe(schedule: Schedule) -> str:
    """Formulation lisible, pour l'interface et les journaux."""
    if not schedule.automatic:
        return "manuelle"
    if schedule.kind == INTERVAL:
        minutes = schedule.minutes or 0
        if minutes % 60 == 0:
            hours = minutes // 60
            return f"toutes les {hours} heure{'s' if hours > 1 else ''}"
        return f"toutes les {minutes} minutes"

    assert schedule.at is not None
    moment = schedule.at.strftime("%H:%M")
    if schedule.kind == DAILY:
        return f"tous les jours à {moment}"

    names = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    selected = ", ".join(names[day - 1] for day in schedule.days)
    return f"{selected} à {moment}"
