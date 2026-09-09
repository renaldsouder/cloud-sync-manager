"""Catalogue des fournisseurs (CLOUD-003, CLOUD-004, §9).

Les métadonnées viennent de ``rclone config providers`` : quels champs
existent, lesquels sont obligatoires, lesquels sont des mots de passe à
obscurcir (``IsPassword``) et lesquels sont simplement sensibles
(``Sensitive``, une clé d'accès S3 par exemple). Rien n'est codé en dur —
un backend ajouté par une future version de rclone est pris en charge sans
modification de notre code.
"""

from __future__ import annotations

from typing import Any

#: Fournisseurs mis en avant dans l'assistant, dans cet ordre (§9.1).
#: Les quatre derniers s'authentifient par clé ou mot de passe : leur
#: parcours est intégralement réalisable dans la WebUI. Les trois premiers
#: exigent un aller-retour OAuth (P9).
PRIORITY_PROVIDERS: tuple[str, ...] = (
    "drive",
    "onedrive",
    "dropbox",
    "s3",
    "b2",
    "webdav",
    "sftp",
)

#: Fournisseurs dont l'autorisation passe par un navigateur.
OAUTH_PROVIDERS: frozenset[str] = frozenset({"drive", "onedrive", "dropbox"})

FRIENDLY_NAMES: dict[str, str] = {
    "drive": "Google Drive",
    "onedrive": "Microsoft OneDrive",
    "dropbox": "Dropbox",
    "s3": "Amazon S3 et compatibles",
    "b2": "Backblaze B2",
    "webdav": "WebDAV",
    "sftp": "SFTP",
}


def shape_option(option: dict[str, Any]) -> dict[str, Any]:
    help_text = str(option.get("Help", ""))
    return {
        "name": option.get("Name"),
        "help": help_text.split("\n", 1)[0],
        "help_full": help_text,
        "type": option.get("Type", "string"),
        "required": bool(option.get("Required")),
        "advanced": bool(option.get("Advanced")),
        "is_password": bool(option.get("IsPassword")),
        "sensitive": bool(option.get("Sensitive")),
        "default": option.get("DefaultStr"),
        "examples": [
            {"value": example.get("Value"), "help": example.get("Help")}
            for example in (option.get("Examples") or [])
        ],
    }


def shape_provider(provider: dict[str, Any], *, include_advanced: bool = False) -> dict[str, Any]:
    name = str(provider.get("Name", ""))
    options = [
        shape_option(option)
        for option in provider.get("Options", [])
        if not option.get("Hide")
    ]
    if not include_advanced:
        options = [option for option in options if not option["advanced"]]
    return {
        "name": name,
        "label": FRIENDLY_NAMES.get(name, str(provider.get("Description", name))),
        "description": str(provider.get("Description", "")),
        "priority": name in PRIORITY_PROVIDERS,
        "needs_oauth": name in OAUTH_PROVIDERS,
        "options": options,
    }


def sort_providers(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Les prioritaires d'abord, dans l'ordre du §9.1, puis le reste A→Z."""

    def key(provider: dict[str, Any]) -> tuple[int, int, str]:
        name = provider["name"]
        if name in PRIORITY_PROVIDERS:
            return (0, PRIORITY_PROVIDERS.index(name), name)
        return (1, 0, provider["label"].lower())

    return sorted(providers, key=key)


def password_fields(provider: dict[str, Any]) -> set[str]:
    """Champs à obscurcir avant écriture dans le ``rclone.conf``."""
    return {
        str(option.get("Name"))
        for option in provider.get("Options", [])
        if option.get("IsPassword")
    }


#: Fragments de noms qui trahissent un identifiant plutôt qu'une coordonnée.
CREDENTIAL_HINTS: tuple[str, ...] = (
    "pass",
    "secret",
    "token",
    "key",
    "credential",
    "auth",
)


def sensitive_fields(provider: dict[str, Any]) -> set[str]:
    """Champs à masquer dans les **logs et diagnostics** (SEC-002).

    Reprend la sémantique de ``rclone config redacted`` : rclone marque
    ``Sensitive`` tout ce qu'il ne veut pas voir apparaître dans un rapport
    de bogue, y compris un nom d'hôte ou un identifiant de connexion.
    """
    return {
        str(option.get("Name"))
        for option in provider.get("Options", [])
        if option.get("IsPassword") or option.get("Sensitive")
    }


def hidden_fields(provider: dict[str, Any]) -> set[str]:
    """Champs à ne jamais renvoyer par l'API (§6.1).

    Plus étroit que :func:`sensitive_fields`. Le §6.1 vise « les secrets,
    jetons et mots de passe » — pas les coordonnées de connexion. rclone
    marque ``host`` et ``user`` comme sensibles pour ses rapports de bogue ;
    les masquer dans notre UI empêcherait l'utilisateur de reconnaître son
    propre serveur au moment de le modifier. On masque donc les mots de
    passe déclarés, plus les champs sensibles dont le nom désigne un
    identifiant (``secret_access_key``, ``token``, ``key_pem``…).
    """
    hidden: set[str] = set()
    for option in provider.get("Options", []):
        name = str(option.get("Name", ""))
        if option.get("IsPassword"):
            hidden.add(name)
        elif option.get("Sensitive") and any(
            hint in name.lower() for hint in CREDENTIAL_HINTS
        ):
            hidden.add(name)
    return hidden
