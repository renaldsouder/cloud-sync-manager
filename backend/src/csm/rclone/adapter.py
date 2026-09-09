"""Adaptateur rclone (CLOUD-002, GLOBAL-002).

Toute la logique métier passe par cette classe : le mécanisme d'exécution
reste ainsi remplaçable sans toucher au reste (décision P3).

Règles tenues ici, et nulle part ailleurs :

- **liste d'arguments, ``shell=False``** — jamais de chaîne shell (§18) ;
- **aucun mot de passe en clair dans ``argv``** — ``obscure`` et la phrase de
  passe de configuration passent par ``stdin`` ;
- la phrase de passe de la configuration voyage par l'environnement
  (``RCLONE_CONFIG_PASS``), jamais en argument.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from csm.rclone.probe import RcloneInfo, rclone_binary

DEFAULT_TIMEOUT = 30.0

#: Catalogue des fournisseurs, mémorisé par chemin de binaire.
_PROVIDER_CACHE: dict[str, list[dict[str, Any]]] = {}


class RcloneError(RuntimeError):
    """Échec d'une commande rclone, cause technique conservée (§27.10)."""

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class RcloneUnavailable(RcloneError):
    """Binaire rclone introuvable."""


@dataclass
class RcloneAdapter:
    binary: str
    config_path: Path
    config_password: str | None = field(default=None, repr=False)

    @classmethod
    def from_settings(cls, settings: Any) -> "RcloneAdapter":
        executable = rclone_binary(settings.rclone_binary)
        if executable is None:
            raise RcloneUnavailable("binaire rclone introuvable")
        return cls(binary=executable, config_path=settings.rclone_config_path)

    # -- exécution ----------------------------------------------------------

    def _environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        if self.config_password:
            environment["RCLONE_CONFIG_PASS"] = self.config_password
        else:
            environment.pop("RCLONE_CONFIG_PASS", None)
        # Empêche rclone de demander quoi que ce soit : dans un conteneur,
        # une invite bloquerait indéfiniment.
        environment["RCLONE_ASK_PASSWORD"] = "false"
        return environment

    def run(
        self,
        arguments: list[str],
        *,
        stdin: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        use_config: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [self.binary]
        if use_config:
            command += ["--config", str(self.config_path)]
        command += arguments

        try:
            return subprocess.run(
                command,
                input=stdin,
                capture_output=True,
                text=True,
                # rclone émet toujours de l'UTF-8. Sans le forcer, Python
                # décoderait avec l'encodage local (cp1252 sous Windows,
                # ASCII sous une locale C) et corromprait les noms de
                # fichiers accentués — GLOBAL-005.
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                shell=False,  # §18 — jamais d'interprétation shell
                env=self._environment(),
            )
        except FileNotFoundError as exc:
            raise RcloneUnavailable(f"binaire rclone introuvable : {self.binary}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RcloneError(f"rclone n'a pas répondu en {timeout} s") from exc

    def _run_checked(self, arguments: list[str], **kwargs: Any) -> str:
        completed = self.run(arguments, **kwargs)
        if completed.returncode != 0:
            raise RcloneError(
                _first_useful_line(completed.stderr) or "échec rclone",
                exit_code=completed.returncode,
            )
        return completed.stdout

    def _run_json(self, arguments: list[str], **kwargs: Any) -> Any:
        raw = self._run_checked(arguments, **kwargs)
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise RcloneError("réponse rclone illisible") from exc

    # -- introspection ------------------------------------------------------

    def version(self) -> RcloneInfo:
        payload = self._run_json(
            ["rc", "--loopback", "core/version"], use_config=False, timeout=15.0
        )
        return RcloneInfo(
            version=str(payload.get("version", "")),
            os=str(payload.get("os", "")),
            arch=str(payload.get("arch", "")),
            go_version=str(payload.get("goVersion", "")),
        )

    def providers(self) -> list[dict[str, Any]]:
        """Catalogue complet des backends (CLOUD-003, CLOUD-004).

        Mémorisé par binaire : le catalogue ne change pas tant que la version
        de rclone ne change pas, et il est consulté à chaque lecture de
        stockage pour savoir quels champs sont secrets.
        """
        cached = _PROVIDER_CACHE.get(self.binary)
        if cached is None:
            cached = list(self._run_json(["config", "providers"], use_config=False))
            _PROVIDER_CACHE[self.binary] = cached
        return cached

    def list_remotes(self) -> list[str]:
        payload = self._run_json(["rc", "--loopback", "config/listremotes"])
        return list(payload.get("remotes", []))

    # -- secrets ------------------------------------------------------------

    def obscure(self, secret: str) -> str:
        """Obscurcit un mot de passe **via stdin**, jamais via ``argv``."""
        return self._run_checked(
            ["obscure", "-"], stdin=f"{secret}\n", use_config=False, timeout=15.0
        ).strip()

    def set_config_password(self, passphrase: str) -> None:
        """Chiffre le fichier de configuration (P8).

        rclone demande la phrase deux fois sur stdin ; c'est le seul moyen
        non interactif de la fournir sans la mettre en ligne de commande.
        """
        self._run_checked(
            ["config", "encryption", "set"],
            stdin=f"{passphrase}\n{passphrase}\n",
            timeout=30.0,
        )
        self.config_password = passphrase

    # -- opérations sur un remote ------------------------------------------

    def lsjson(
        self,
        remote_path: str,
        *,
        dirs_only: bool = False,
        max_depth: int = 1,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> list[dict[str, Any]]:
        """Listing structuré (CLOUD-005)."""
        arguments = ["lsjson", remote_path, "--max-depth", str(max_depth)]
        if dirs_only:
            arguments.append("--dirs-only")
        return list(self._run_json(arguments, timeout=timeout))

    def about(self, remote: str, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any] | None:
        """Quotas du stockage. ``None`` si le backend ne sait pas répondre.

        Une connexion réussie ne signifie pas que toutes les opérations sont
        supportées (§9.3) : l'absence d'``about`` n'est pas une erreur.
        """
        completed = self.run(["about", remote, "--json"], timeout=timeout)
        if completed.returncode != 0:
            return None
        try:
            return dict(json.loads(completed.stdout))
        except ValueError:
            return None


def _first_useful_line(stderr: str) -> str:
    """Extrait la cause technique la plus parlante de la sortie d'erreur.

    Le §27.10 interdit de masquer une erreur rclone derrière « Échec » : on
    conserve la ligne d'origine pour le diagnostic, la couche API se charge
    d'en présenter une version compréhensible.
    """
    for line in reversed([line.strip() for line in stderr.splitlines() if line.strip()]):
        lowered = line.lower()
        if "failed to" in lowered or "error" in lowered or "couldn't" in lowered:
            return line
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return lines[-1] if lines else ""
