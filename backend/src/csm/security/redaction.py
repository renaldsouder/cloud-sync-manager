"""Filtre de secrets centralisé (SEC-002, §14).

Point de passage **unique** pour tout ce qui sort de l'application : logs,
réponses d'API, historique, export de configuration, package de diagnostic.
Un secret qui échappe à ce module échappe à tout le reste — d'où des tests
sur des jetons réalistes plutôt que sur des chaînes inventées.
"""

from __future__ import annotations

import re
from typing import Any, Callable

MASK = "***REDACTED***"

#: Noms de clés considérés comme secrets, quel que soit le format rencontré
#: (INI rclone, JSON, paramètre d'URL, variable d'environnement, flag CLI).
SECRET_KEYS = (
    "password",
    "pass",
    "passphrase",
    "secret",
    "client_secret",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "secret_access_key",
    "auth",
    "authorization",
    "credential",
    "credentials",
    "key",
)

_KEY_ALT = "|".join(sorted(SECRET_KEYS, key=len, reverse=True))

Rule = tuple[re.Pattern[str], Callable[[re.Match[str]], str]]

_RULES: tuple[Rule, ...] = (
    # JSON :  "client_secret": "xxx"   "token": {...}
    (
        re.compile(rf"(?i)(\"(?:{_KEY_ALT})\"\s*:\s*)(\{{.*?\}}|\"[^\"]*\"|[^,\s}}]+)"),
        lambda m: f'{m.group(1)}"{MASK}"',
    ),
    # INI rclone / .env / CLI :  token = {...}   password: xxx   --pass xxx
    # L'alternative « Bearer/Basic » doit précéder ``\S+`` : sans elle,
    # « Authorization: Bearer <jwt> » ne masquerait que le mot « Bearer »
    # et laisserait le jeton en clair.
    (
        re.compile(
            rf"(?i)(\b(?:--)?(?:{_KEY_ALT})\b)(\s*=\s*|\s*:\s*|\s+)"
            rf"(\{{.*?\}}|\"[^\"]*\"|'[^']*'|(?:Bearer|Basic)\s+\S+|\S+)"
        ),
        lambda m: f"{m.group(1)}{m.group(2)}{MASK}",
    ),
    # En-tête HTTP :  Authorization: Bearer xxx
    (
        re.compile(r"(?i)\b(Bearer|Basic)\s+([A-Za-z0-9\-._~+/=]{8,})"),
        lambda m: f"{m.group(1)} {MASK}",
    ),
    # URL signée :  ?sig=xxx  &X-Amz-Signature=xxx
    (
        re.compile(
            r"(?i)([?&](?:sig|signature|x-amz-signature|x-amz-credential|se|sp|sv)=)"
            r"([^&\s]+)"
        ),
        lambda m: f"{m.group(1)}{MASK}",
    ),
    # Userinfo dans une URL :  sftp://user:motdepasse@hote
    (
        re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+:)([^@/\s]+)(@)"),
        lambda m: f"{m.group(1)}{MASK}{m.group(3)}",
    ),
)


def redact(value: str | None) -> str | None:
    """Masque tout secret reconnaissable dans ``value``."""
    if not value:
        return value
    out = value
    for pattern, replacement in _RULES:
        out = pattern.sub(replacement, out)
    return out


def redact_mapping(data: Any) -> Any:
    """Applique la redaction récursivement sur une structure JSON.

    Une clé secrète est masquée **entièrement, sans regarder sa valeur** :
    c'est ce qui protège les jetons qui ne ressemblent à rien de particulier.
    """
    if isinstance(data, dict):
        return {
            key: (MASK if is_secret_key(key) else redact_mapping(value))
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [redact_mapping(item) for item in data]
    if isinstance(data, tuple):
        return tuple(redact_mapping(item) for item in data)
    if isinstance(data, str):
        return redact(data)
    return data


def is_secret_key(key: str) -> bool:
    return key.strip().lower().lstrip("-").replace("-", "_") in SECRET_KEYS
