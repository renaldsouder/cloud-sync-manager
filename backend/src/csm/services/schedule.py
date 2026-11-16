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
#: Mode avancé (PLAN-004) : une expression cron à cinq champs. Jamais
#: proposée par défaut — le §11 veut un parcours standard sans cron.
CRON = "cron"
KINDS = frozenset({MANUAL, INTERVAL, DAILY, WEEKLY, CRON})

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
    #: Expression cron, pour ``kind == "cron"`` (PLAN-004).
    cron: str | None = None
    #: Fenêtre d'exclusion (PLAN-005) : aucune exécution planifiée ne
    #: démarre entre ``quiet_start`` et ``quiet_end``, heure du serveur. La
    #: fenêtre peut enjamber minuit (22:00 → 06:00).
    quiet_start: time | None = None
    quiet_end: time | None = None

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

        cron: str | None = None
        if kind == CRON:
            raw_cron = payload.get("cron")
            if not isinstance(raw_cron, str) or not raw_cron.strip():
                raise ScheduleError("une expression cron est nécessaire")
            cron = " ".join(raw_cron.split())
            CronExpression.parse(cron)  # refus immédiat d'une expression illisible

        quiet_start = quiet_end = None
        quiet = payload.get("quiet")
        if quiet:
            if not isinstance(quiet, dict):
                raise ScheduleError("fenêtre d'exclusion illisible")
            quiet_start = _parse_time(quiet.get("start"))
            quiet_end = _parse_time(quiet.get("end"))
            if quiet_start is None or quiet_end is None:
                raise ScheduleError("la fenêtre d'exclusion demande un début et une fin")
            if quiet_start == quiet_end:
                raise ScheduleError(
                    "une fenêtre d'exclusion de durée nulle — ou de vingt-quatre "
                    "heures — n'a pas de sens"
                )

        return cls(
            kind=kind,
            minutes=minutes,
            at=at,
            days=days,
            catch_up=catch_up,
            cron=cron,
            quiet_start=quiet_start,
            quiet_end=quiet_end,
        )

    def to_dict(self) -> dict:
        payload: dict = {"kind": self.kind, "catch_up": self.catch_up}
        if self.minutes is not None:
            payload["minutes"] = self.minutes
        if self.at is not None:
            payload["time"] = self.at.strftime("%H:%M")
        if self.days:
            payload["days"] = list(self.days)
        if self.cron:
            payload["cron"] = self.cron
        if self.quiet_start is not None and self.quiet_end is not None:
            payload["quiet"] = {
                "start": self.quiet_start.strftime("%H:%M"),
                "end": self.quiet_end.strftime("%H:%M"),
            }
        return payload

    # -- fenêtre d'exclusion (PLAN-005) -------------------------------------

    def is_quiet(self, moment: datetime) -> bool:
        """``moment`` tombe-t-il dans la fenêtre d'exclusion ?"""
        if self.quiet_start is None or self.quiet_end is None:
            return False
        current = moment.time().replace(second=0, microsecond=0)
        if self.quiet_start < self.quiet_end:
            return self.quiet_start <= current < self.quiet_end
        # Fenêtre qui enjambe minuit.
        return current >= self.quiet_start or current < self.quiet_end

    def leave_quiet(self, moment: datetime) -> datetime:
        """Premier instant hors de la fenêtre, à partir de ``moment``."""
        if not self.is_quiet(moment):
            return moment
        assert self.quiet_end is not None
        end = moment.replace(
            hour=self.quiet_end.hour, minute=self.quiet_end.minute, second=0, microsecond=0
        )
        if end <= moment:
            end += timedelta(days=1)
        return end

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

    candidate = _raw_occurrence(schedule, after or now)
    # PLAN-005 — une échéance qui tombe dans la fenêtre d'exclusion est
    # repoussée à sa fin, une seule fois : pas de rafale à la réouverture.
    return schedule.leave_quiet(candidate) if candidate is not None else None


def _raw_occurrence(schedule: Schedule, reference: datetime) -> datetime | None:
    if schedule.kind == INTERVAL:
        assert schedule.minutes is not None
        return reference + timedelta(minutes=schedule.minutes)

    if schedule.kind == CRON:
        assert schedule.cron is not None
        return CronExpression.parse(schedule.cron).next_after(reference)

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
    base = _describe_kind(schedule)
    if schedule.automatic and schedule.quiet_start and schedule.quiet_end:
        base += (
            f", jamais entre {schedule.quiet_start.strftime('%H:%M')}"
            f" et {schedule.quiet_end.strftime('%H:%M')}"
        )
    return base


def _describe_kind(schedule: Schedule) -> str:
    if not schedule.automatic:
        return "manuelle"
    if schedule.kind == CRON:
        return f"cron « {schedule.cron} »"
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


# -- expression cron (PLAN-004) ----------------------------------------------

#: Bornes des cinq champs : minute, heure, jour du mois, mois, jour de la
#: semaine (0 et 7 = dimanche, comme le cron classique).
_CRON_FIELDS = (
    ("minute", 0, 59),
    ("heure", 0, 23),
    ("jour du mois", 1, 31),
    ("mois", 1, 12),
    ("jour de la semaine", 0, 7),
)

#: Horizon de recherche : au-delà, l'expression ne se déclenche jamais
#: (« 30 février »). Quatre ans couvrent le 29 février.
_CRON_HORIZON_DAYS = 4 * 366


@dataclass(frozen=True)
class CronExpression:
    """Sous-ensemble délibérément sobre du cron : nombres, ``*``, listes,
    intervalles et pas (``*/15``, ``1-5``, ``0,30``). Ni noms de jours ni
    raccourcis ``@daily`` : ce mode s'adresse à qui connaît déjà cron, et
    chaque syntaxe de plus est une occasion de se tromper sur ce qu'elle
    déclenche.
    """

    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]
    #: Le cron classique combine jour du mois et jour de la semaine par un
    #: OU quand les deux sont restreints, et par un ET sinon.
    dom_restricted: bool
    dow_restricted: bool

    @classmethod
    def parse(cls, expression: str) -> "CronExpression":
        parts = expression.split()
        if len(parts) != 5:
            raise ScheduleError(
                "une expression cron compte cinq champs : minute heure "
                "jour-du-mois mois jour-de-la-semaine"
            )
        values = [
            _cron_field(part, name, low, high)
            for part, (name, low, high) in zip(parts, _CRON_FIELDS)
        ]
        dow = frozenset(0 if day == 7 else day for day in values[4])
        parsed = cls(
            minutes=values[0],
            hours=values[1],
            days_of_month=values[2],
            months=values[3],
            days_of_week=dow,
            dom_restricted=parts[2] != "*",
            dow_restricted=parts[4] != "*",
        )
        if parsed.next_after(datetime(2000, 1, 1)) is None:
            raise ScheduleError("cette expression cron ne se déclenche jamais")
        return parsed

    def _day_matches(self, moment: datetime) -> bool:
        if moment.month not in self.months:
            return False
        dom = moment.day in self.days_of_month
        dow = (moment.isoweekday() % 7) in self.days_of_week
        if self.dom_restricted and self.dow_restricted:
            return dom or dow
        return dom and dow

    def next_after(self, reference: datetime) -> datetime | None:
        """Premier déclenchement **strictement après** ``reference``."""
        start = reference.replace(second=0, microsecond=0) + timedelta(minutes=1)
        day = start.replace(hour=0, minute=0)
        for offset in range(_CRON_HORIZON_DAYS):
            current = day + timedelta(days=offset)
            if not self._day_matches(current):
                continue
            for hour in sorted(self.hours):
                for minute in sorted(self.minutes):
                    candidate = current.replace(hour=hour, minute=minute)
                    if candidate >= start:
                        return candidate
        return None


def _cron_field(part: str, name: str, low: int, high: int) -> frozenset[int]:
    values: set[int] = set()
    for item in part.split(","):
        step = 1
        if "/" in item:
            item, raw_step = item.split("/", 1)
            step = _cron_int(raw_step, name)
            if step < 1:
                raise ScheduleError(f"cron, {name} : pas invalide")
        if item == "*":
            first, last = low, high
        elif "-" in item:
            raw_first, raw_last = item.split("-", 1)
            first, last = _cron_int(raw_first, name), _cron_int(raw_last, name)
        else:
            first = last = _cron_int(item, name)
            if step != 1:
                last = high
        if not (low <= first <= high and low <= last <= high) or first > last:
            raise ScheduleError(f"cron, {name} : hors des bornes {low}-{high}")
        values.update(range(first, last + 1, step))
    return frozenset(values)


def _cron_int(value: str, name: str) -> int:
    if not value.isdigit():
        raise ScheduleError(f"cron, {name} : « {value} » n'est pas un nombre")
    return int(value)
