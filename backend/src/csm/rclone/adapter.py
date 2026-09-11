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


#: Code de sortie par lequel bisync signale des listings absents ou
#: inutilisables. Ce n'est pas une panne mais une demande de
#: ré-initialisation : le confondre avec une erreur générique priverait
#: l'utilisateur de la seule action qui débloque la situation (§27.10).
BISYNC_NEEDS_RESYNC = 7

#: Nom des fichiers témoins que ``--check-access`` attend des deux côtés.
BISYNC_CHECK_FILENAME = "RCLONE_TEST"

#: Arbitrages acceptés. ``none`` conserve les deux versions au lieu d'en
#: élire une : aucune donnée n'est perdue, mais le nom d'origine disparaît
#: au profit des deux copies suffixées — ce que l'interface doit annoncer.
BISYNC_CONFLICT_RESOLVE = frozenset(
    {"none", "path1", "path2", "newer", "older", "larger", "smaller"}
)

#: Sort réservé à la version perdante. ``delete`` la détruit : il n'est
#: accepté ici que parce que le §8 impose de l'exposer explicitement,
#: jamais par défaut.
BISYNC_CONFLICT_LOSER = frozenset({"num", "pathname", "delete"})

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
        """Environnement remis à plat avant chaque appel.

        rclone interprète **toute** variable ``RCLONE_*`` comme une option :
        ``RCLONE_MAX_DELETE``, ``RCLONE_DELETE_EXCLUDED``, ``RCLONE_FILTER``…
        En laissant passer l'environnement du conteneur, une variable posée
        par l'administrateur — ou une faute de frappe dans le template
        Unraid — modifierait silencieusement une opération destructive, et
        notre ligne de commande cesserait d'être la vérité entière. Le §18
        veut l'inverse : ce qui est demandé à rclone est ce que nous avons
        écrit, et rien d'autre.

        On repart donc de l'environnement débarrassé de tout ``RCLONE_*``,
        puis on ne remet que les deux réglages dont nous avons besoin.
        """
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("RCLONE_")
        }
        if self.config_password:
            environment["RCLONE_CONFIG_PASS"] = self.config_password
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
        filter_file: str | None = None,
        extra: list[str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> list[dict[str, Any]]:
        """Listing structuré (CLOUD-005, FILT-005)."""
        arguments = ["lsjson", remote_path, "--max-depth", str(max_depth)]
        if dirs_only:
            arguments.append("--dirs-only")
        if filter_file:
            arguments += ["--filter-from", filter_file]
        arguments += extra or []
        return list(self._run_json(arguments, timeout=timeout))

    # -- transferts ---------------------------------------------------------

    def build_transfer_args(
        self,
        operation: str,
        source: str,
        destination: str,
        *,
        dry_run: bool = False,
        transfers: int | None = None,
        checkers: int | None = None,
        bwlimit: str | None = None,
        backup_dir: str | None = None,
        filter_file: str | None = None,
        extra: list[str] | None = None,
    ) -> list[str]:
        """Construit la ligne de commande d'un transfert.

        Fonction pure et donc testable : le §20.3 exige que la simulation
        soit cohérente avec l'exécution réelle, et c'est vérifiable ici —
        les deux listes ne diffèrent que par ``--dry-run``.
        """
        if operation not in {"copy", "sync"}:
            raise ValueError(f"opération non supportée : {operation}")

        arguments = [
            operation,
            source,
            destination,
            "--use-json-log",
            "--log-level",
            "INFO",
            "--stats",
            "1s",
            "--stats-log-level",
            "NOTICE",
        ]
        if dry_run:
            arguments.append("--dry-run")
        if transfers:
            arguments += ["--transfers", str(transfers)]
        if checkers:
            arguments += ["--checkers", str(checkers)]
        if bwlimit:
            arguments += ["--bwlimit", bwlimit]
        if backup_dir:
            arguments += ["--backup-dir", backup_dir]
        if filter_file:
            # Un fichier plutôt que des options répétées : pas de limite
            # de longueur de ligne de commande, et le jeu de règles reste
            # inspectable après coup pour le diagnostic (§14).
            arguments += ["--filter-from", filter_file]
        arguments += extra or []
        return arguments

    def build_bisync_args(
        self,
        path1: str,
        path2: str,
        workdir: str,
        *,
        dry_run: bool = False,
        resync: bool = False,
        check_access: bool = False,
        conflict_resolve: str = "none",
        conflict_loser: str = "num",
        conflict_suffix: str = "conflict",
        max_delete: int | None = None,
        backup_dir1: str | None = None,
        backup_dir2: str | None = None,
        filter_file: str | None = None,
        transfers: int | None = None,
        checkers: int | None = None,
        bwlimit: str | None = None,
        extra: list[str] | None = None,
    ) -> list[str]:
        """Construit la ligne de commande d'une synchronisation bidirectionnelle.

        Fonction pure, comme ``build_transfer_args`` : le §20.3 exige qu'une
        simulation corresponde exactement à l'exécution réelle, et c'est
        vérifiable ici — les deux listes ne diffèrent que par ``--dry-run``.

        Plusieurs choix sont imposés par le comportement mesuré de rclone
        1.75.1, pas par préférence :

        ``workdir`` est **obligatoire**. bisync y range les listings de
        l'exécution précédente, sans lesquels il refuse de tourner et exige
        une ré-initialisation. Son emplacement par défaut est un cache
        utilisateur, qui disparaît avec le conteneur : le laisser là
        transformerait chaque recréation de conteneur en ``--resync`` forcé.

        ``--color NEVER`` n'est pas cosmétique. bisync colore sa sortie même
        lorsqu'elle est redirigée, y compris à l'intérieur du journal JSON :
        sans cela, des séquences d'échappement se retrouveraient enregistrées
        dans les chemins de fichiers de l'historique.

        Les options de sauvegarde s'appellent ``--backup-dir1`` et
        ``--backup-dir2`` — une par côté — et le fichier de filtres
        ``--filters-file``, là où les transferts unidirectionnels utilisent
        ``--backup-dir`` et ``--filter-from``.
        """
        if not workdir:
            raise ValueError("bisync exige un répertoire de travail persistant")
        if conflict_resolve not in BISYNC_CONFLICT_RESOLVE:
            raise ValueError(f"arbitrage de conflit inconnu : {conflict_resolve}")
        if conflict_loser not in BISYNC_CONFLICT_LOSER:
            raise ValueError(f"sort du perdant inconnu : {conflict_loser}")

        arguments = [
            "bisync",
            path1,
            path2,
            "--workdir",
            workdir,
            "--color",
            "NEVER",
            "--use-json-log",
            "--log-level",
            "INFO",
            "--stats",
            "1s",
            "--stats-log-level",
            "NOTICE",
            "--conflict-resolve",
            conflict_resolve,
            "--conflict-loser",
            conflict_loser,
            "--conflict-suffix",
            conflict_suffix,
        ]
        if resync:
            arguments.append("--resync")
        if dry_run:
            arguments.append("--dry-run")
        if check_access:
            arguments += ["--check-access", "--check-filename", BISYNC_CHECK_FILENAME]
        if max_delete is not None:
            arguments += ["--max-delete", str(max_delete)]
        if backup_dir1:
            arguments += ["--backup-dir1", backup_dir1]
        if backup_dir2:
            arguments += ["--backup-dir2", backup_dir2]
        if filter_file:
            arguments += ["--filters-file", filter_file]
        if transfers:
            arguments += ["--transfers", str(transfers)]
        if checkers:
            arguments += ["--checkers", str(checkers)]
        if bwlimit:
            arguments += ["--bwlimit", bwlimit]
        arguments += extra or []
        return arguments

    def start(self, arguments: list[str]) -> subprocess.Popen[str]:
        """Démarre un transfert et rend la main immédiatement.

        ``stdout`` est ignoré : rclone n'y écrit rien en mode journal JSON.
        Tout passe par ``stderr``, lu ligne à ligne par l'appelant.
        """
        command = [self.binary, "--config", str(self.config_path), *arguments]
        try:
            return subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
                env=self._environment(),
            )
        except FileNotFoundError as exc:
            raise RcloneUnavailable(f"binaire rclone introuvable : {self.binary}") from exc

    def purge(self, path: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        """Supprime récursivement un dossier.

        Opération irréversible : le seul appelant est la purge de quarantaine,
        qui vérifie la forme du chemin avant d'arriver ici (voir
        ``guards.is_quarantine_path``).
        """
        self._run_checked(["purge", path], timeout=timeout)

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
