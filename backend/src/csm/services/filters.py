"""Filtres d'inclusion et d'exclusion (FILT-001 → FILT-005, §12).

Les règles sont **ordonnées** et la première qui correspond l'emporte : c'est
la sémantique de rclone, et la reproduire à l'identique est la seule façon
d'expliquer honnêtement à l'utilisateur pourquoi un fichier est retenu ou
écarté (FILT-005).

Deux conséquences de cette sémantique méritent d'être connues :

- dès qu'une règle d'inclusion existe, tout ce qui n'est pas explicitement
  inclus doit être exclu — sans quoi « n'inclure que les photos » ne
  filtrerait rien du tout ;
- les tailles ne sont pas des règles de motif : rclone les traite par des
  options séparées, appliquées en plus du jeu ordonné.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

EXCLUDE_PATH = "exclude_path"
INCLUDE_PATH = "include_path"
EXCLUDE_EXT = "exclude_ext"
INCLUDE_EXT = "include_ext"
EXCLUDE_NAME = "exclude_name"
INCLUDE_NAME = "include_name"
HIDDEN = "hidden"
MIN_SIZE = "min_size"
MAX_SIZE = "max_size"

PATTERN_TYPES = (
    EXCLUDE_PATH,
    INCLUDE_PATH,
    EXCLUDE_EXT,
    INCLUDE_EXT,
    EXCLUDE_NAME,
    INCLUDE_NAME,
    HIDDEN,
)
SIZE_TYPES = (MIN_SIZE, MAX_SIZE)
RULE_TYPES = frozenset(PATTERN_TYPES + SIZE_TYPES)

INCLUDING = frozenset({INCLUDE_PATH, INCLUDE_EXT, INCLUDE_NAME})

SIZE_PATTERN = re.compile(r"^\d+(\.\d+)?\s*[BKMGTPbkmgtp]?$")


class FilterError(ValueError):
    """Règle invalide, message destiné à l'utilisateur."""


@dataclass(frozen=True)
class Rule:
    type: str
    value: str

    @property
    def including(self) -> bool:
        return self.type in INCLUDING


@dataclass(frozen=True)
class CompiledFilters:
    #: Lignes du fichier passé à ``--filter-from``, dans l'ordre.
    lines: tuple[str, ...] = ()
    #: Options séparées : ``--min-size``, ``--max-size``.
    flags: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.lines and not self.flags

    def as_text(self) -> str:
        return "\n".join(self.lines) + ("\n" if self.lines else "")


def parse_rules(payload: object) -> list[Rule]:
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise FilterError("les règles doivent former une liste")

    rules: list[Rule] = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise FilterError("chaque règle doit être un objet")
        kind = str(entry.get("type", ""))
        value = str(entry.get("value", "")).strip()
        if kind not in RULE_TYPES:
            raise FilterError(f"type de règle inconnu : {kind}")
        if kind == HIDDEN:
            value = value or "exclude"
            if value != "exclude":
                raise FilterError("la règle « hidden » n'accepte que « exclude »")
        elif not value:
            raise FilterError(f"la règle « {kind} » attend une valeur")
        elif kind in SIZE_TYPES and not SIZE_PATTERN.match(value):
            raise FilterError(
                f"taille invalide : {value!r} — attendu par exemple « 10M » ou « 1G »"
            )
        rules.append(Rule(type=kind, value=value))
    return rules


def _patterns_for(rule: Rule) -> list[str]:
    """Motifs rclone correspondant à une règle, dans l'ordre."""
    if rule.type == HIDDEN:
        return [".*", "**/.*"]
    if rule.type in (EXCLUDE_EXT, INCLUDE_EXT):
        extension = rule.value.lstrip(".")
        return [f"*.{extension}"]
    if rule.type in (EXCLUDE_NAME, INCLUDE_NAME):
        return [rule.value]

    value = rule.value.strip("/")
    if any(character in value for character in "*?["):
        return [value]
    # Un dossier désigné sans joker vise son contenu : « Cache » doit
    # écarter Cache/ et tout ce qu'il renferme, pas un fichier nommé Cache.
    return [f"{value}/**"]


def compile_filters(rules: list[Rule]) -> CompiledFilters:
    lines: list[str] = []
    flags: list[str] = []
    has_include = False

    for rule in rules:
        if rule.type == MIN_SIZE:
            flags += ["--min-size", rule.value]
            continue
        if rule.type == MAX_SIZE:
            flags += ["--max-size", rule.value]
            continue

        sign = "+" if rule.including else "-"
        has_include = has_include or rule.including
        for pattern in _patterns_for(rule):
            lines.append(f"{sign} {pattern}")

    if has_include:
        # Sans cette ligne finale, « n'inclure que les photos » ne filtrerait
        # rien : tout ce qui n'est pas nommé resterait pris par défaut.
        lines.append("- **")

    return CompiledFilters(lines=tuple(lines), flags=tuple(flags))


def explain(path: str, rules: list[Rule]) -> tuple[bool, str]:
    """Première règle qui décide du sort de ``path`` (FILT-005).

    Reproduit l'ordre de rclone. C'est une **explication**, pas l'autorité :
    la liste réellement retenue est toujours celle que rclone renvoie, et
    l'interface confronte les deux plutôt que de faire confiance à celle-ci.
    """
    name = path.rsplit("/", 1)[-1]
    has_include = any(rule.including for rule in rules)

    for rule in rules:
        if rule.type in SIZE_TYPES:
            continue
        for pattern in _patterns_for(rule):
            if _matches(path, name, pattern):
                verb = "inclus" if rule.including else "exclu"
                return rule.including, f"{verb} par la règle « {rule.type} : {rule.value} »"

    if has_include:
        return False, "exclu : aucune règle d'inclusion ne le désigne"
    return True, "inclus : aucune règle ne s'y applique"


def _matches(path: str, name: str, pattern: str) -> bool:
    """Approximation des motifs rclone avec les outils de la bibliothèque."""
    if pattern.startswith("**/"):
        return fnmatch.fnmatch(name, pattern[3:])
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(f"{prefix}/")
    if "/" in pattern:
        return fnmatch.fnmatch(path, pattern)
    return fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(path, pattern)
