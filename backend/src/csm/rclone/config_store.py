"""Propriété du fichier ``rclone.conf`` (SEC-001, §6.1).

L'application **écrit elle-même** le fichier INI plutôt que de passer par
``rclone config create``. Raison : les options d'un remote se passeraient
alors en ``argv``, y compris les mots de passe. Même obscurcie, une valeur
en ligne de commande reste réversible par ``rclone reveal``.

Le mot de passe en clair ne transite donc jamais autrement que par **stdin**,
vers ``rclone obscure -``. Le §6.1 le rappelle : l'obfuscation n'est pas du
chiffrement — la protection réelle vient des permissions du fichier et, en
option, d'une phrase de passe (``config encryption set``, elle aussi
alimentée par stdin).
"""

from __future__ import annotations

import configparser
import os
import stat
import time
from pathlib import Path

ENCRYPTED_MARKER = "RCLONE_ENCRYPT_V0:"


class ConfigEncrypted(RuntimeError):
    """La configuration est chiffrée : rclone doit la lire à notre place."""


class RcloneConfigStore:
    """Lecture/écriture du ``rclone.conf``, hors secrets en ligne de commande."""

    def __init__(self, path: Path) -> None:
        self.path = path

    # -- état ---------------------------------------------------------------

    def exists(self) -> bool:
        return self.path.is_file()

    def is_encrypted(self) -> bool:
        if not self.exists():
            return False
        with self.path.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(256)
        return ENCRYPTED_MARKER in head

    def ensure_exists(self) -> None:
        """Crée un fichier vide aux permissions strictes."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()
        self._harden()

    def _harden(self) -> None:
        """0600 — lisible du seul propriétaire. Sans effet utile sous Windows."""
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:  # pragma: no cover - dépend du système de fichiers
            pass

    # -- contenu ------------------------------------------------------------

    def _parser(self) -> configparser.ConfigParser:
        # interpolation désactivée : une valeur rclone peut contenir « % ».
        return configparser.ConfigParser(interpolation=None)

    def read(self) -> configparser.ConfigParser:
        if self.is_encrypted():
            raise ConfigEncrypted(
                "configuration chiffrée : lecture directe impossible"
            )
        parser = self._parser()
        if self.exists():
            parser.read(self.path, encoding="utf-8")
        return parser

    def sections(self) -> list[str]:
        return self.read().sections()

    def has_section(self, name: str) -> bool:
        return name in self.read()

    def upsert_section(self, name: str, values: dict[str, str]) -> None:
        """Crée ou remplace intégralement une section.

        Le remplacement est total : une option retirée par l'utilisateur ne
        doit pas survivre en douce dans le fichier.
        """
        parser = self.read()
        if parser.has_section(name):
            parser.remove_section(name)
        parser.add_section(name)
        for key, value in values.items():
            parser.set(name, key, value)
        self._write(parser)

    def remove_section(self, name: str) -> bool:
        parser = self.read()
        removed = parser.remove_section(name)
        if removed:
            self._write(parser)
        return removed

    def _write(self, parser: configparser.ConfigParser) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            parser.write(handle, space_around_delimiters=True)
        try:
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:  # pragma: no cover
            pass
        self._replace_atomically(temporary)
        self._harden()

    def _replace_atomically(self, temporary: Path, attempts: int = 5) -> None:
        """Remplace le fichier en place, sans état intermédiaire visible.

        Jamais de ``rclone.conf`` tronqué si le conteneur s'arrête au mauvais
        moment : on écrit à côté, puis on substitue d'un seul geste.

        Sous Windows, cette substitution échoue par intermittence avec
        ``ERROR_ACCESS_DENIED`` quand un antivirus ou l'indexeur tient
        brièvement le fichier qui vient d'être écrit — observé une fois sur
        neuf sur la suite de tests. Sous Linux, seul environnement de
        production, ``rename(2)`` est atomique et ignore ce cas. On réessaie
        donc brièvement plutôt que de faire échouer l'enregistrement d'un
        stockage sur un aléa de poste de développement.
        """
        delay = 0.05
        for remaining in range(attempts - 1, -1, -1):
            try:
                os.replace(temporary, self.path)
                return
            except PermissionError:
                if remaining == 0:
                    raise
                time.sleep(delay)
                delay *= 2
