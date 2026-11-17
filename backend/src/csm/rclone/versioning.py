"""Ce que le fournisseur garde de lui-même (CONF-005).

Plusieurs fournisseurs conservent les fichiers supprimés ou les versions
précédentes d'un fichier remplacé : corbeille de Google Drive, historique
de versions de OneDrive et de Dropbox, versions cachées de Backblaze B2. Ce
module le dit à l'utilisateur, stockage par stockage, **d'après la
configuration réelle** du stockage — une option ``hard_delete`` ou
``use_trash`` change tout.

Trois règles :

* **Ne jamais le supposer.** Un fournisseur inconnu, ou un S3 dont le
  versioning dépend du bucket, est décrit comme « inconnu », pas comme
  protégé. Les durées de conservation dépendent de l'offre souscrite et des
  règles de l'administrateur : on les cite comme ordre de grandeur, jamais
  comme garantie.
* **Ne rien retirer.** La corbeille de l'application, les seuils et la
  simulation obligatoire s'appliquent à l'identique, que le fournisseur
  garde des versions ou non. Ce n'est qu'un filet de plus — et le seul
  quand on le découvre trop tard.
* **Aucun appel réseau.** Tout se déduit du type et des options déjà
  présentes dans le ``rclone.conf``.
"""

from __future__ import annotations

from typing import Any

#: Valeurs de ``deleted`` et ``replaced`` : ce que devient un fichier que
#: rclone supprime, ou qu'il remplace par une version plus récente.
KEPT = "kept"  # récupérable chez le fournisseur
PERMANENT = "permanent"  # perdu, sauf corbeille de l'application
UNKNOWN = "unknown"  # dépend d'un réglage que l'application ne voit pas


def _flag(options: dict[str, str], name: str, default: bool) -> bool:
    raw = options.get(name)
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() in {"true", "1", "yes", "on"}


def describe_versioning(provider: str, options: dict[str, str] | None) -> dict[str, Any]:
    """Comportement du fournisseur face à une suppression ou un remplacement."""
    options = options or {}

    if provider == "drive":
        if _flag(options, "use_trash", True):
            return _out(
                KEPT,
                KEPT,
                "Google Drive place les fichiers supprimés dans sa corbeille "
                "(environ 30 jours) et conserve les révisions précédentes d'un "
                "fichier remplacé, selon ses propres règles.",
            )
        return _out(
            PERMANENT,
            KEPT,
            "L'option use_trash est désactivée sur ce stockage : un fichier "
            "supprimé par une tâche ne passe pas par la corbeille de Google "
            "Drive.",
        )

    if provider == "onedrive":
        if _flag(options, "hard_delete", False):
            return _out(
                PERMANENT,
                KEPT,
                "L'option hard_delete est activée sur ce stockage : un fichier "
                "supprimé par une tâche ne passe pas par la corbeille de "
                "OneDrive.",
            )
        return _out(
            KEPT,
            KEPT,
            "OneDrive place les fichiers supprimés dans sa corbeille et garde "
            "un historique des versions. Durées et nombre de versions dépendent "
            "du compte, et de l'administrateur pour un compte professionnel.",
        )

    if provider == "dropbox":
        return _out(
            KEPT,
            KEPT,
            "Dropbox permet de récupérer un fichier supprimé et les versions "
            "précédentes d'un fichier, pendant une durée qui dépend de l'offre.",
        )

    if provider == "b2":
        if _flag(options, "hard_delete", False):
            return _out(
                PERMANENT,
                KEPT,
                "L'option hard_delete est activée sur ce stockage : un fichier "
                "supprimé par une tâche est effacé, sans version cachée.",
            )
        return _out(
            KEPT,
            KEPT,
            "Backblaze B2 conserve les versions précédentes et masque un fichier "
            "supprimé au lieu de l'effacer — sauf si une règle de cycle de vie "
            "du bucket les purge.",
        )

    if provider == "s3":
        return _out(
            UNKNOWN,
            UNKNOWN,
            "Dépend du bucket : seul un bucket dont le versioning est activé "
            "chez le fournisseur garde les versions précédentes. "
            "Vérifiez-le dans sa console.",
        )

    if provider in {"sftp", "webdav", "local", "ftp", "smb"}:
        return _out(
            PERMANENT,
            PERMANENT,
            "Ce type de stockage ne conserve pas de lui-même les fichiers "
            "supprimés ou remplacés, sauf dispositif propre au serveur.",
        )

    return _out(
        UNKNOWN,
        UNKNOWN,
        "L'application ne sait pas si ce fournisseur conserve les fichiers "
        "supprimés ou remplacés : ne comptez pas dessus.",
    )


def _out(deleted: str, replaced: str, detail: str) -> dict[str, Any]:
    return {"deleted": deleted, "replaced": replaced, "detail": detail}
