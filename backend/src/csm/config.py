"""Configuration applicative.

Tout ce qui doit survivre au conteneur vit sous ``config_dir`` (§16), qui
correspond à l'appdata Unraid monté sur ``/config`` (UNRAID-003, DATA-001).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CSM_", extra="ignore")

    #: Racine de l'appdata. Tout état persistant en découle.
    config_dir: Path = Path("/config")

    #: Port de la WebUI. 3572 : aucune collision sur les 4298 applications
    #: du catalogue Community Applications (cf. docs/Propositions_Techniques_Unraid.md).
    port: int = 3572
    host: str = "0.0.0.0"

    #: Racines locales autorisées, séparées par des virgules. Aucune tâche ne
    #: peut désigner un chemin en dehors de ces racines (LOCAL-005).
    allowed_roots: str = "/mnt/user"

    #: Build du frontend servi par l'API (un seul conteneur, un seul port).
    web_dir: Path = Path("/app/web")

    #: Chemin explicite du binaire rclone. Vide ⇒ recherche dans le ``PATH``,
    #: ce qui est le cas dans l'image. Utile en développement pour pointer une
    #: copie locale (``backend/.tools/rclone.exe``).
    rclone_binary: str | None = None

    #: Phrase de passe du ``rclone.conf`` chiffré (P8). Vide ⇒ configuration
    #: non chiffrée, protégée par les seules permissions du fichier (0600),
    #: ce qui est le comportement par défaut décrit au §6.1.
    rclone_config_password: str | None = None

    #: Rétention de la quarantaine (§16, CONF-004). Une corbeille n'est
    #: purgée que si elle dépasse cette ancienneté **et** n'est pas parmi les
    #: dernières conservées.
    quarantine_retention_days: int = 30
    quarantine_keep_runs: int = 3

    #: Rétention de l'historique d'exécutions (LOG-005, §13). Même règle
    #: que la quarantaine : ancienneté **et** nombre à conserver.
    history_retention_days: int = 90
    history_keep_runs: int = 200

    #: Battement du planificateur. Désactivable pour les tests, qui pilotent
    #: leur propre instance avec une horloge fixe.
    scheduler_enabled: bool = True
    scheduler_poll_seconds: float = 20.0

    log_level: str = "INFO"

    @property
    def db_path(self) -> Path:
        return self.config_dir / "db" / "cloudsyncmanager.sqlite"

    @property
    def rclone_config_path(self) -> Path:
        return self.config_dir / "rclone.conf"

    @property
    def log_dir(self) -> Path:
        return self.config_dir / "logs"

    @property
    def roots(self) -> tuple[Path, ...]:
        return tuple(
            Path(part.strip())
            for part in self.allowed_roots.split(",")
            if part.strip()
        )

    def ensure_dirs(self) -> None:
        """Crée l'arborescence appdata. Idempotent."""
        for directory in (self.config_dir, self.db_path.parent, self.log_dir):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
