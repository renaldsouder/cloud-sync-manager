"""Garde-fous destructifs (§8, CONF-001, CONF-002, CONF-004).

Trois protections indépendantes, dans cet ordre :

1. **Source inaccessible ou anormalement vide** ⇒ échec sans propager (§8.2).
2. **Seuil de suppression** évalué sur une simulation de contrôle, *avant*
   qu'un seul fichier ne bouge (§8.3).
3. **Quarantaine** : les suppressions sont des déplacements, donc réversibles
   (CONF-004, P13).

Une note sur `--max-delete`, qui semblait être le garde-fou idéal : rclone
supprime **jusqu'au seuil puis abandonne**. Mesuré : avec `--max-delete 2`
pour trois suppressions, deux fichiers avaient déjà disparu avant l'arrêt.
Ce n'est donc pas un pré-contrôle et il ne peut pas tenir le rôle principal.
On le passe quand même en seconde ceinture — il borne les dégâts si la
réalité diverge de la simulation entre les deux passes — mais la décision
de bloquer se prend ici, avant toute écriture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from csm.rclone.adapter import RcloneAdapter, RcloneError

#: Nombre de chemins conservés pour l'écran de confirmation (§10.4).
MAX_LISTED_PATHS = 1000


class GuardBlocked(RuntimeError):
    """Exécution refusée par un garde-fou. Le message est destiné à l'utilisateur."""


@dataclass
class DeletionPlan:
    """Ce qu'une exécution réelle ferait, mesuré par simulation."""

    deletes: int = 0
    #: Nombre d'objets de la destination examinés. rclone compte dans
    #: ``checks`` chaque objet de la destination : conservé ou supprimé.
    #: C'est donc la bonne base pour un pourcentage.
    checks: int = 0
    transfers: int = 0
    paths: list[str] = field(default_factory=list)

    @property
    def percent(self) -> float:
        if self.checks <= 0:
            return 100.0 if self.deletes else 0.0
        return round(self.deletes / self.checks * 100, 1)


def check_source_available(
    adapter: RcloneAdapter, source: str, destination: str
) -> None:
    """§8.2 — en cas de doute sur la source, on ne supprime rien.

    Le scénario redouté est le share démonté : la source répond « vide »,
    et un Miroir naïf en conclut qu'il faut vider la destination.
    """
    try:
        source_entries = adapter.lsjson(source, max_depth=1)
    except RcloneError as exc:
        raise GuardBlocked(f"source inaccessible : {exc}") from exc

    if source_entries:
        return

    try:
        destination_entries = adapter.lsjson(destination, max_depth=1)
    except RcloneError as exc:
        raise GuardBlocked(f"destination inaccessible : {exc}") from exc

    if destination_entries:
        raise GuardBlocked(
            "la source ne contient aucun fichier alors que la destination en "
            "contient : le partage est probablement démonté ou inaccessible. "
            "Aucune suppression n'a été effectuée."
        )


#: En deçà de ce nombre de suppressions, le critère en pourcentage ne
#: s'applique pas. Sur une destination de deux fichiers, en retirer un fait
#: 50 % sans que rien d'anormal ne se produise : appliquer le pourcentage là
#: bloquerait sans cesse des opérations légitimes, et un garde-fou qu'on
#: apprend à contourner ne protège plus de rien. En dessous du plancher,
#: c'est le seuil absolu qui gouverne.
MIN_DELETES_FOR_PERCENT = 5


def evaluate_threshold(
    plan: DeletionPlan, *, max_deletes: int | None, max_delete_percent: int | None
) -> str | None:
    """§8.3 — motif du blocage, ou ``None`` si l'exécution peut se poursuivre."""
    if plan.deletes <= 0:
        return None

    if max_deletes is not None and plan.deletes > max_deletes:
        return (
            f"{plan.deletes} suppressions prévues, au-delà du seuil de "
            f"{max_deletes} fixé pour cette tâche."
        )

    if (
        max_delete_percent is not None
        and plan.deletes > MIN_DELETES_FOR_PERCENT
        and plan.percent > max_delete_percent
    ):
        return (
            f"{plan.deletes} suppressions prévues, soit {plan.percent} % de la "
            f"destination, au-delà du seuil de {max_delete_percent} %."
        )

    return None


def quarantine_directory(destination: str, when: datetime | None = None) -> str:
    """Emplacement de la corbeille pour cette exécution (CONF-004).

    Placée **dans** la destination et exclue de la synchronisation : c'est la
    seule forme qui fonctionne aussi quand la destination est une racine, où
    il n'existe aucun dossier frère où se replier.
    """
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    base = destination.rstrip("/")
    separator = "" if base.endswith(":") else "/"
    return f"{base}{separator}.cloudsync-trash/{stamp}"


#: Motif d'exclusion à passer en même temps que ``--backup-dir``, sans quoi
#: rclone refuse le chevauchement entre destination et corbeille.
QUARANTINE_EXCLUDE = "/.cloudsync-trash/**"
