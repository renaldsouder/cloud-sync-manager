"""Notifications (NOTIF-001 → NOTIF-004, §15, P10).

Deux canaux, tous deux optionnels :

- **Unraid**, via l'API GraphQL intégrée à l'OS depuis la 7.2. C'est la seule
  voie propre : l'ancienne méthode — monter ``/usr/local/emhttp`` pour
  appeler le script ``notify`` — imposerait un montage du système hôte,
  contraire au §5.3 et à SEC-004.
- **Webhook** générique, pour Home Assistant, Discord ou autre.

Règle d'or : une notification ne doit **jamais** faire échouer une tâche.
Tout est capturé, journalisé, et l'exécution continue. Une alerte manquée
est ennuyeuse ; une synchronisation qui échoue parce qu'un webhook est
tombé serait absurde.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

from csm.security.redaction import redact

logger = logging.getLogger("csm.notifications")

FAILURE = "failure"
BLOCKED = "blocked"
SUCCESS = "success"
AUTH = "auth_expired"
EVENTS = (FAILURE, BLOCKED, AUTH, SUCCESS)

#: Le §15 veut des alertes utiles, pas du bruit : par défaut on ne signale
#: que ce qui demande une action.
DEFAULT_EVENTS = (FAILURE, BLOCKED, AUTH)

IMPORTANCE = {FAILURE: "ALERT", BLOCKED: "WARNING", AUTH: "WARNING", SUCCESS: "INFO"}

#: `notifyIfUnique` plutôt que `createNotification` : une tâche qui échoue
#: toutes les heures ne doit pas remplir le centre de notifications.
UNRAID_MUTATION = (
    "mutation($input: NotificationData!) { notifyIfUnique(input: $input) { id } }"
)

TIMEOUT = 8.0


@dataclass(frozen=True)
class NotificationConfig:
    unraid_url: str | None = None
    unraid_api_key: str | None = field(default=None, repr=False)
    webhook_url: str | None = None
    events: tuple[str, ...] = DEFAULT_EVENTS

    @property
    def configured(self) -> bool:
        return bool((self.unraid_url and self.unraid_api_key) or self.webhook_url)

    def wants(self, event: str) -> bool:
        return event in self.events


class Notifier:
    """Envoi best-effort, sans jamais propager d'erreur."""

    def __init__(self, config: NotificationConfig) -> None:
        self.config = config

    def notify(self, event: str, subject: str, description: str) -> bool:
        if not self.config.configured or not self.config.wants(event):
            return False

        # Le message peut contenir une sortie rclone : il passe par le
        # filtre de secrets comme tout ce qui quitte l'application (SEC-002).
        subject = redact(subject) or subject
        description = redact(description) or description

        delivered = False
        if self.config.unraid_url and self.config.unraid_api_key:
            delivered |= self._send_unraid(event, subject, description)
        if self.config.webhook_url:
            delivered |= self._send_webhook(event, subject, description)
        return delivered

    # -- transports ---------------------------------------------------------

    def _send_unraid(self, event: str, subject: str, description: str) -> bool:
        payload = {
            "query": UNRAID_MUTATION,
            "variables": {
                "input": {
                    "title": "Cloud Sync Manager",
                    "subject": subject,
                    "description": description,
                    "importance": IMPORTANCE.get(event, "WARNING"),
                }
            },
        }
        url = self.config.unraid_url.rstrip("/")  # type: ignore[union-attr]
        if not url.endswith("/graphql"):
            url = f"{url}/graphql"
        return self._post(
            url,
            payload,
            headers={"x-api-key": self.config.unraid_api_key or ""},
            label="Unraid",
        )

    def _send_webhook(self, event: str, subject: str, description: str) -> bool:
        payload = {
            "source": "cloud-sync-manager",
            "event": event,
            "subject": subject,
            "description": description,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        return self._post(self.config.webhook_url or "", payload, label="webhook")

    def _post(
        self, url: str, payload: dict, *, headers: dict[str, str] | None = None, label: str
    ) -> bool:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                text = response.read(4096).decode("utf-8", "replace")
                if '"errors"' in text:
                    # §27.10 : conserver la cause technique plutôt que « échec ».
                    logger.warning("%s a répondu une erreur : %s", label, redact(text))
                    return False
                return 200 <= response.status < 300
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning("notification %s non délivrée : %s", label, exc)
            return False


def summarise_run(task_name: str, status: str, detail: str | None) -> tuple[str, str]:
    """Sujet et description d'une exécution terminée."""
    labels = {
        "error": "a échoué",
        "warning": "s'est terminée avec des avertissements",
        "blocked": "est bloquée et attend une validation",
        "success": "s'est terminée avec succès",
        "interrupted": "a été interrompue",
    }
    subject = f"« {task_name} » {labels.get(status, status)}"
    return subject, detail or subject


def event_for(status: str) -> str | None:
    """Événement de notification correspondant à un état d'exécution."""
    if status in {"error", "warning"}:
        return FAILURE
    if status == "blocked":
        return BLOCKED
    if status == "success":
        return SUCCESS
    return None
