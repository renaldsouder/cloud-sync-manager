"""Paramètres généraux persistés en base (§6, table ``settings``).

Séparés de :class:`csm.config.Settings`, qui porte la configuration
d'infrastructure venue de l'environnement. Ici vivent les réglages que
l'utilisateur modifie depuis la WebUI : notifications, préférences.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from csm.db.models import Setting
from csm.services.notifications import DEFAULT_EVENTS, EVENTS, NotificationConfig

UNRAID_URL = "notifications.unraid_url"
UNRAID_API_KEY = "notifications.unraid_api_key"
WEBHOOK_URL = "notifications.webhook_url"
NOTIFY_EVENTS = "notifications.events"
ALLOW_SELF_SIGNED = "notifications.allow_self_signed"
LOG_LEVEL = "logging.level"

#: Clés dont la valeur ne doit jamais ressortir de l'application (§6.1).
SECRET_KEYS = frozenset({UNRAID_API_KEY})

#: Niveaux de journalisation (LOG-003, §14). Les valeurs stockées restent
#: en ASCII : elles voyagent dans du JSON et dans des URL.
LOG_LEVELS = ("normal", "detaille", "diagnostic")
DEFAULT_LOG_LEVEL = "normal"

#: Verbosité demandée à rclone. rclone ne connaît que DEBUG, INFO, NOTICE et
#: ERROR : « normal » et « détaillé » partagent donc INFO, et ce qui les
#: distingue est ce que *nous* conservons — voir ``NARRATED_LEVELS``.
RCLONE_LOG_LEVEL = {
    "normal": "INFO",
    "detaille": "INFO",
    "diagnostic": "DEBUG",
}

#: Niveaux où le déroulé annoncé par rclone est conservé en base, en plus des
#: transferts, suppressions, conflits et erreurs. C'est du volume pur : il ne
#: s'enregistre que si quelqu'un l'a demandé.
NARRATED_LEVELS = frozenset({"detaille", "diagnostic"})


def get(session: Session, key: str) -> str | None:
    row = session.get(Setting, key)
    return row.value if row else None


def put(session: Session, key: str, value: str | None) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value


def all_settings(session: Session) -> dict[str, str | None]:
    return {row.key: row.value for row in session.scalars(select(Setting)).all()}


def public_settings(session: Session) -> dict[str, object]:
    """Réglages tels qu'on peut les renvoyer : secrets remplacés par un état."""
    stored = all_settings(session)
    return {
        "unraid_url": stored.get(UNRAID_URL) or "",
        "unraid_api_key_configured": bool(stored.get(UNRAID_API_KEY)),
        "webhook_url": stored.get(WEBHOOK_URL) or "",
        "events": _events(stored.get(NOTIFY_EVENTS)),
        "allow_self_signed": stored.get(ALLOW_SELF_SIGNED) == "1",
    }


def log_level(session: Session) -> str:
    """Niveau de journalisation en vigueur (LOG-003).

    Une valeur inconnue — base éditée à la main, réglage d'une version
    ultérieure — retombe sur « normal » plutôt que d'interrompre une
    exécution : le niveau de logs ne doit jamais empêcher de synchroniser.
    """
    stored = get(session, LOG_LEVEL)
    return stored if stored in LOG_LEVELS else DEFAULT_LOG_LEVEL


def logging_settings(session: Session) -> dict[str, object]:
    return {"level": log_level(session)}


def update_logging(session: Session, *, level: str | None = None) -> None:
    if level is None:
        return
    if level not in LOG_LEVELS:
        raise ValueError(f"niveau de journalisation inconnu : {level}")
    put(session, LOG_LEVEL, level)


def notification_config(session: Session) -> NotificationConfig:
    stored = all_settings(session)
    return NotificationConfig(
        unraid_url=stored.get(UNRAID_URL) or None,
        unraid_api_key=stored.get(UNRAID_API_KEY) or None,
        webhook_url=stored.get(WEBHOOK_URL) or None,
        events=tuple(_events(stored.get(NOTIFY_EVENTS))),
        allow_self_signed=stored.get(ALLOW_SELF_SIGNED) == "1",
    )


def update_notifications(
    session: Session,
    *,
    unraid_url: str | None = None,
    unraid_api_key: str | None = None,
    webhook_url: str | None = None,
    events: list[str] | None = None,
    allow_self_signed: bool | None = None,
) -> None:
    if unraid_url is not None:
        put(session, UNRAID_URL, unraid_url.strip() or None)
    if webhook_url is not None:
        put(session, WEBHOOK_URL, webhook_url.strip() or None)
    if unraid_api_key is not None:
        # Chaîne vide = effacement explicite ; absence = on ne touche pas,
        # ce qui permet d'enregistrer le formulaire sans retaper la clé.
        put(session, UNRAID_API_KEY, unraid_api_key.strip() or None)
    if allow_self_signed is not None:
        put(session, ALLOW_SELF_SIGNED, "1" if allow_self_signed else "0")

    if events is not None:
        unknown = [event for event in events if event not in EVENTS]
        if unknown:
            raise ValueError(f"événement inconnu : {', '.join(unknown)}")
        put(session, NOTIFY_EVENTS, json.dumps(events))


def _events(raw: str | None) -> list[str]:
    if not raw:
        return list(DEFAULT_EVENTS)
    try:
        parsed = json.loads(raw)
    except ValueError:
        return list(DEFAULT_EVENTS)
    if not isinstance(parsed, list):
        return list(DEFAULT_EVENTS)
    return [event for event in parsed if event in EVENTS]
