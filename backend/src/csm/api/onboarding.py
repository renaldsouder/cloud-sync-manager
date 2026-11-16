"""Assistant de première utilisation (FIRST-001, §3).

Le §3 décrit un parcours : ajouter un stockage, le tester, choisir un dossier
local et un dossier distant, simuler, puis valider. Chaque étape existait
déjà ; il manquait ce qui les enchaîne et dit où l'on en est.

L'état est **déduit** de ce que contient l'application, jamais d'un compteur
que l'on incrémenterait. Une étape cochée alors que le stockage a été
supprimé entre-temps donnerait une piste fausse au moment où l'utilisateur en
a le plus besoin.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from csm.api.deps import get_session
from csm.db.models import Remote, Task, TaskRun
from csm.api.auth import is_enabled
from csm.services import settings_store

router = APIRouter(tags=["démarrage"])

#: Clé de l'abandon volontaire. L'assistant disparaît quand l'utilisateur le
#: demande, même si le parcours n'est pas terminé : insister ferait de l'aide
#: une gêne.
DISMISSED_KEY = "onboarding.dismissed"


class OnboardingOut(BaseModel):
    """Où en est l'utilisateur, étape par étape."""

    #: Interface protégée par un mot de passe (SEC-006).
    password: bool = False
    #: Au moins un stockage distant existe.
    remote: bool = False
    #: Ce stockage a répondu au moins une fois.
    remote_tested: bool = False
    #: Au moins une tâche est configurée.
    task: bool = False
    #: Une simulation a été menée à son terme (§8.1, FIRST-004).
    simulation: bool = False

    dismissed: bool = False
    #: Vrai lorsque tout le parcours du §3 est accompli.
    completed: bool = False


@router.get("/onboarding", response_model=OnboardingOut)
def read_onboarding(
    request: Request, session: Session = Depends(get_session)
) -> OnboardingOut:
    remotes = list(session.scalars(select(Remote)).all())
    tasks = list(session.scalars(select(Task).where(Task.one_shot.is_(False))).all())

    simulation = session.scalar(
        select(TaskRun.id)
        .where(TaskRun.dry_run.is_(True))
        .where(TaskRun.status == "success")
        .limit(1)
    )

    etat = OnboardingOut(
        password=is_enabled(session),
        remote=bool(remotes),
        remote_tested=any(remote.status == "ok" for remote in remotes),
        task=bool(tasks),
        simulation=simulation is not None,
        dismissed=settings_store.get(session, DISMISSED_KEY) == "1",
    )
    etat.completed = (
        etat.password
        and etat.remote_tested
        and etat.task
        and etat.simulation
    )
    return etat


@router.post("/onboarding/dismiss", response_model=OnboardingOut)
def dismiss_onboarding(
    request: Request, session: Session = Depends(get_session)
) -> OnboardingOut:
    settings_store.put(session, DISMISSED_KEY, "1")
    session.commit()
    return read_onboarding(request, session)
