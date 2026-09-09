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

Corollaire du §27.10 : chaque tentative rend une cause exploitable, pas un
booléen. Un utilisateur ne doit pas avoir à lire les journaux du conteneur
pour apprendre que son certificat n'est pas vérifiable.
"""

from __future__ import annotations

import json
import logging
import ssl
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


@dataclass
class DeliveryResult:
    channel: str
    ok: bool
    detail: str

    @property
    def label(self) -> str:
        return f"{self.channel} : {self.detail}"


@dataclass(frozen=True)
class NotificationConfig:
    unraid_url: str | None = None
    unraid_api_key: str | None = field(default=None, repr=False)
    webhook_url: str | None = None
    events: tuple[str, ...] = DEFAULT_EVENTS
    #: Accepte un certificat que l'autorité ne valide pas. Nécessaire pour
    #: joindre un serveur Unraid en HTTPS, qui présente par défaut un
    #: certificat auto-signé. Le trafic reste chiffré ; c'est l'identité du
    #: serveur qui n'est plus vérifiée — acceptable vers sa propre machine
    #: sur son propre réseau, à condition que ce soit un choix conscient.
    allow_self_signed: bool = False

    @property
    def configured(self) -> bool:
        return bool((self.unraid_url and self.unraid_api_key) or self.webhook_url)

    def wants(self, event: str) -> bool:
        return event in self.events


class Notifier:
    """Envoi best-effort, sans jamais propager d'erreur."""

    def __init__(self, config: NotificationConfig) -> None:
        self.config = config

    def notify(self, event: str, subject: str, description: str) -> list[DeliveryResult]:
        if not self.config.configured or not self.config.wants(event):
            return []

        # Le message peut contenir une sortie rclone : il passe par le
        # filtre de secrets comme tout ce qui quitte l'application (SEC-002).
        subject = redact(subject) or subject
        description = redact(description) or description

        results: list[DeliveryResult] = []
        if self.config.unraid_url and self.config.unraid_api_key:
            results.append(self._send_unraid(event, subject, description))
        if self.config.webhook_url:
            results.append(self._send_webhook(event, subject, description))
        return results

    # -- transports ---------------------------------------------------------

    def _send_unraid(self, event: str, subject: str, description: str) -> DeliveryResult:
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
            channel="Unraid",
        )

    def _send_webhook(self, event: str, subject: str, description: str) -> DeliveryResult:
        payload = {
            "source": "cloud-sync-manager",
            "event": event,
            "subject": subject,
            "description": description,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        return self._post(self.config.webhook_url or "", payload, channel="Webhook")

    def _context(self, url: str) -> ssl.SSLContext | None:
        if url.startswith("https://") and self.config.allow_self_signed:
            return ssl._create_unverified_context()
        return None

    def _post(
        self,
        url: str,
        payload: dict,
        *,
        headers: dict[str, str] | None = None,
        channel: str,
    ) -> DeliveryResult:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(
                request, timeout=TIMEOUT, context=self._context(url)
            ) as response:
                text = response.read(8192).decode("utf-8", "replace")
                if '"errors"' in text:
                    detail = _graphql_error(text)
                    logger.warning("%s a répondu une erreur : %s", channel, detail)
                    return DeliveryResult(channel, False, detail)
                if 200 <= response.status < 300:
                    return DeliveryResult(channel, True, "notification acceptée")
                return DeliveryResult(channel, False, f"réponse HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            detail = _http_error(exc)
            logger.warning("notification %s refusée : %s", channel, detail)
            return DeliveryResult(channel, False, detail)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            detail = _transport_error(exc)
            logger.warning("notification %s non délivrée : %s", channel, detail)
            return DeliveryResult(channel, False, detail)


def _transport_error(exc: Exception) -> str:
    """Traduit une panne de transport en cause actionnable (§27.10)."""
    text = str(getattr(exc, "reason", exc)) or exc.__class__.__name__

    if "CERTIFICATE_VERIFY_FAILED" in text or "self-signed" in text.lower():
        return (
            "certificat non vérifiable — un serveur Unraid en HTTPS présente "
            "par défaut un certificat auto-signé. Cochez « accepter un "
            "certificat auto-signé », ou utilisez une adresse en http://"
        )
    if "Name or service not known" in text or "nodename nor servname" in text:
        return "nom d'hôte introuvable — vérifiez l'adresse du serveur"
    if "Connection refused" in text:
        return "connexion refusée — vérifiez l'adresse, le port et que le service écoute"
    if "timed out" in text.lower():
        return f"pas de réponse en {TIMEOUT:.0f} s — serveur injoignable depuis le conteneur"
    return text


def _http_error(exc: urllib.error.HTTPError) -> str:
    if exc.code in (401, 403):
        return (
            f"HTTP {exc.code} — clé d'API refusée ou permissions insuffisantes "
            "(NOTIFICATIONS:CREATE_ANY est nécessaire)"
        )
    if exc.code == 404:
        return f"HTTP 404 — point d'entrée GraphQL introuvable à cette adresse"
    try:
        body = exc.read(2048).decode("utf-8", "replace")
    except Exception:  # pragma: no cover - le corps peut être absent
        body = ""
    return f"HTTP {exc.code}{' — ' + redact(body) if body else ''}"


def _graphql_error(text: str) -> str:
    """Extrait le message d'erreur GraphQL plutôt que de rendre tout le corps."""
    try:
        payload = json.loads(text)
        messages = [
            str(error.get("message", "")).strip()
            for error in payload.get("errors", [])
            if isinstance(error, dict)
        ]
        joined = " ; ".join(message for message in messages if message)
        if joined:
            return redact(joined) or joined
    except ValueError:
        pass
    return redact(text[:300]) or "réponse GraphQL en erreur"


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
