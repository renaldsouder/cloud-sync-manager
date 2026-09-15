"""Niveaux de journalisation (LOG-003, §14).

Trois niveaux, et une seule chose qui compte vraiment : que le diagnostic
puisse être demandé quand il faut, et qu'il ne s'enregistre pas quand il ne
faut pas. Un journal exhaustif laissé en permanence ferait grossir la base
sans que personne ne le lise.

Le niveau « détaillé » ne change rien à ce que rclone écrit — rclone ne
connaît que DEBUG, INFO, NOTICE et ERROR. Ce qu'il change, c'est ce que
*nous* conservons : le déroulé annoncé par rclone, jeté au niveau normal.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.rclone.adapter import RcloneAdapter
from csm.rclone.events import RcloneEvent
from csm.security.redaction import MASK
from csm.services import runner as runner_module
from csm.services import settings_store
from tests.test_tasks_api import make_cloud, make_task, start, wait_for


def niveau(client: TestClient) -> str:
    return client.get("/api/settings").json()["logging"]["level"]


def regler(client: TestClient, valeur: str):
    return client.put("/api/settings", json={"log_level": valeur})


def rclone_level(arguments: list[str]) -> str:
    return arguments[arguments.index("--log-level") + 1]


@pytest.fixture
def adapter(tmp_path: Path) -> RcloneAdapter:
    return RcloneAdapter(binary="rclone", config_path=tmp_path / "rclone.conf")


# -- le réglage ---------------------------------------------------------------


def test_the_default_level_is_normal(client: TestClient) -> None:
    """Le diagnostic ne s'active jamais tout seul : c'est du volume."""
    assert niveau(client) == "normal"


def test_the_available_levels_are_announced(client: TestClient) -> None:
    assert client.get("/api/settings").json()["log_levels"] == [
        "normal",
        "detaille",
        "diagnostic",
    ]


@pytest.mark.parametrize("choix", ["normal", "detaille", "diagnostic"])
def test_a_level_can_be_chosen_and_survives(client: TestClient, choix: str) -> None:
    assert regler(client, choix).status_code == 200
    assert niveau(client) == choix


def test_an_unknown_level_is_refused(client: TestClient) -> None:
    refus = regler(client, "verbeux")
    assert refus.status_code == 409
    assert "verbeux" in refus.json()["detail"]
    assert niveau(client) == "normal", "le refus ne doit rien avoir écrit"


def test_saving_the_notifications_does_not_touch_the_level(
    client: TestClient,
) -> None:
    """Absent = inchangé : le formulaire des notifications ne doit pas
    remettre la journalisation à zéro en passant."""
    regler(client, "diagnostic")
    assert client.put("/api/settings", json={"webhook_url": ""}).status_code == 200
    assert niveau(client) == "diagnostic"


def test_an_unreadable_level_falls_back_to_normal(client: TestClient) -> None:
    """Base éditée à la main, ou réglage d'une version ultérieure restauré
    depuis une sauvegarde : le niveau de logs ne doit jamais empêcher de
    synchroniser."""
    session = client.app.state.session_factory()
    try:
        settings_store.put(session, settings_store.LOG_LEVEL, "loquace")
        session.commit()
        assert settings_store.log_level(session) == "normal"
    finally:
        session.close()


# -- ce que rclone reçoit -----------------------------------------------------


def test_rclone_is_asked_for_info_by_default(adapter: RcloneAdapter) -> None:
    assert rclone_level(adapter.build_transfer_args("copy", "/src", "d:")) == "INFO"


@pytest.mark.parametrize(
    ("choisi", "attendu"),
    [("normal", "INFO"), ("detaille", "INFO"), ("diagnostic", "DEBUG")],
)
def test_the_product_level_maps_to_an_rclone_level(
    choisi: str, attendu: str
) -> None:
    assert settings_store.RCLONE_LOG_LEVEL[choisi] == attendu


def test_an_unknown_rclone_level_is_refused(adapter: RcloneAdapter) -> None:
    """rclone refuserait la valeur au démarrage, et la tâche échouerait pour
    une raison sans aucun rapport avec la synchronisation."""
    with pytest.raises(ValueError, match="journalisation"):
        adapter.build_transfer_args("copy", "/src", "d:", log_level="VERBEUX")
    with pytest.raises(ValueError, match="journalisation"):
        adapter.build_bisync_args("un:", "deux:", "/work", log_level="VERBEUX")


def test_bisync_receives_the_level_too(adapter: RcloneAdapter) -> None:
    args = adapter.build_bisync_args("un:", "deux:", "/work", log_level="DEBUG")
    assert rclone_level(args) == "DEBUG"


def test_the_simulation_keeps_the_same_verbosity(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """§20.3 — mesurer avec une commande différente de celle qui agira ne
    prouverait rien, et la verbosité en fait partie."""
    plan = runner_module.RunPlan(
        operation="sync",
        source=str(local_root),
        destination="cloud:",
        dry_run=False,
        log_level="diagnostic",
    )
    manager = client.app.state.run_manager
    mesure = manager._simulation_arguments(plan)
    assert rclone_level(mesure) == "DEBUG"


# -- ce qui est conservé ------------------------------------------------------


def evenements(client: TestClient, run_id: str, kind: str | None = None) -> list[dict]:
    params = {"kind": kind} if kind else None
    return client.get(f"/api/runs/{run_id}/events", params=params).json()


@pytest.fixture
def tache_deja_passee(
    client: TestClient, tmp_path: Path, local_root: Path
) -> str:
    """Une tâche dont une exécution a déjà tout copié.

    Le second passage n'a donc plus rien à transférer, et rclone l'annonce —
    c'est précisément ce déroulé que « détaillé » conserve.
    """
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Photos"
    source.mkdir()
    (source / "a.txt").write_text("un", encoding="utf-8")
    task = make_task(client, remote_id, source)
    wait_for(client, start(client, task["id"], dry_run=False))
    return str(task["id"])


@pytest.mark.usefixtures("rclone_path")
def test_the_normal_level_keeps_only_what_changed(
    client: TestClient, tache_deja_passee: str
) -> None:
    run = wait_for(client, start(client, tache_deja_passee, dry_run=False))
    assert run["status"] == "success"
    assert evenements(client, run["id"], "other") == []


@pytest.mark.usefixtures("rclone_path")
def test_the_detailed_level_keeps_the_narration(
    client: TestClient, tache_deja_passee: str
) -> None:
    regler(client, "detaille")
    run = wait_for(client, start(client, tache_deja_passee, dry_run=False))

    messages = [e["message"] for e in evenements(client, run["id"], "other")]
    assert messages, "le déroulé de rclone devait être conservé"
    assert any("nothing to transfer" in m for m in messages)


@pytest.mark.usefixtures("rclone_path")
def test_the_diagnostic_level_keeps_the_internals(
    client: TestClient, tache_deja_passee: str
) -> None:
    regler(client, "diagnostic")
    run = wait_for(client, start(client, tache_deja_passee, dry_run=False))

    messages = [e["message"] for e in evenements(client, run["id"], "other")]
    # rclone n'annonce qu'en DEBUG qu'il a comparé puis ignoré un fichier :
    # c'est exactement ce qu'on vient chercher quand une tâche « ne fait
    # rien » sans dire pourquoi.
    assert any("Unchanged" in m for m in messages), messages


def test_a_debug_line_is_never_counted_as_an_operation() -> None:
    """La copie d'un fichier passe par un « .partial » puis un « renamed
    to: » annoncé en DEBUG. Sans coupure, le diagnostic compterait chaque
    fichier deux fois — le compteur dépendrait de la verbosité."""
    from csm.rclone import events as rclone_events

    renommage = rclone_events.parse_line(
        '{"level":"debug","msg":"renamed to: a.txt",'
        '"object":"a.txt.7e5957cd.partial"}'
    )
    assert renommage is not None
    assert renommage.kind == rclone_events.OTHER

    reel = rclone_events.parse_line(
        '{"level":"info","msg":"Copied (new)","object":"a.txt"}'
    )
    assert reel is not None
    assert reel.kind == rclone_events.TRANSFER


def test_a_retry_announced_in_debug_is_not_an_error() -> None:
    """rclone réessaie seul et le raconte en DEBUG. Compter ces lignes
    ferait virer au « Avertissement » une exécution parfaitement réussie,
    pour la seule raison qu'on l'observait de plus près."""
    from csm.rclone import events as rclone_events

    tentative = rclone_events.parse_line(
        '{"level":"debug","msg":"failed to open source object: essai 1"}'
    )
    assert tentative is not None
    assert tentative.kind == rclone_events.OTHER


@pytest.mark.usefixtures("rclone_path")
def test_the_counters_stay_exact_whatever_the_level(
    client: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Le niveau de logs décrit ; il ne doit rien changer à ce qui est fait."""
    regler(client, "diagnostic")
    remote_id = make_cloud(client, tmp_path / "cloud")
    source = local_root / "Photos"
    source.mkdir()
    for index in range(3):
        (source / f"{index}.txt").write_text("x", encoding="utf-8")

    task = make_task(client, remote_id, source)
    run = wait_for(client, start(client, task["id"], dry_run=False))

    assert run["status"] == "success"
    assert run["transferred_files"] == 3
    assert len(evenements(client, run["id"], "transfer")) == 3


@pytest.mark.usefixtures("rclone_path")
def test_the_level_used_is_recorded_on_the_run(
    client: TestClient, tache_deja_passee: str
) -> None:
    """Sans cela, un historique pauvre passerait pour une exécution muette."""
    regler(client, "diagnostic")
    run = wait_for(client, start(client, tache_deja_passee, dry_run=False))
    assert run["summary"]["log_level"] == "diagnostic"


@pytest.mark.usefixtures("rclone_path")
def test_a_level_changed_mid_run_does_not_split_the_history(
    client: TestClient, tache_deja_passee: str
) -> None:
    """Le niveau est figé au lancement : le changer pendant qu'une exécution
    tourne donnerait un historique à moitié détaillé, illisible."""
    regler(client, "detaille")
    run_id = start(client, tache_deja_passee, dry_run=False)
    regler(client, "normal")
    run = wait_for(client, run_id)

    assert run["summary"]["log_level"] == "detaille"
    assert evenements(client, run["id"], "other")


@pytest.mark.usefixtures("rclone_path")
def test_the_ceiling_still_holds_in_diagnostic(
    client: TestClient,
    tache_deja_passee: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§13 — c'est au niveau le plus bavard que le plafond compte le plus.

    Sans lui, une seule exécution en diagnostic sur une grosse arborescence
    suffirait à faire enfler la base sans limite.
    """
    monkeypatch.setattr(runner_module, "MAX_STORED_EVENTS", 2)
    regler(client, "diagnostic")
    run = wait_for(client, start(client, tache_deja_passee, dry_run=False))

    stockes = evenements(client, run["id"])
    assert len(stockes) == 3, "deux événements, plus l'aveu de troncature"
    assert stockes[-1]["kind"] == "warning"
    assert runner_module.TRUNCATION_NOTICE in stockes[-1]["message"]
    assert client.get(f"/api/runs/{run['id']}/events/summary").json()["truncated"]


# -- ce qui ne doit jamais s'y trouver ----------------------------------------


def test_a_secret_in_an_rclone_message_is_masked(client: TestClient) -> None:
    """SEC-002 — le diagnostic fait parler rclone bien davantage, y compris
    de ses URL. Un jeton qui atteint l'historique atteint aussi l'export et
    le package de diagnostic.
    """
    manager = client.app.state.run_manager
    live = runner_module.LiveRun(
        run_id="r1",
        task_id="t1",
        task_name="Photos",
        dry_run=False,
        started_at=runner_module.datetime.now(runner_module.timezone.utc),
        log_level="diagnostic",
    )
    buffer: list = []
    manager._handle(
        live,
        RcloneEvent(
            kind="error",
            level="error",
            message="failed to get: https://api.exemple/f?sig=abc123XYZ",
            path="a.txt",
        ),
        buffer,
        0,
    )

    assert "abc123XYZ" not in buffer[0].message
    assert MASK in buffer[0].message
    assert live.last_error is not None and "abc123XYZ" not in live.last_error


def test_a_filename_is_never_mangled_by_the_masking(client: TestClient) -> None:
    """Le chemin n'est pas filtré, et c'est voulu : « token liste.txt » est un
    nom de fichier ordinaire. Le masquer rendrait l'historique inutilisable
    au moment précis où l'on y cherche un fichier."""
    manager = client.app.state.run_manager
    live = runner_module.LiveRun(
        run_id="r1",
        task_id="t1",
        task_name="Photos",
        dry_run=False,
        started_at=runner_module.datetime.now(runner_module.timezone.utc),
    )
    buffer: list = []
    manager._handle(
        live,
        RcloneEvent(
            kind="transfer",
            level="info",
            message="Copied (new)",
            path="Documents/token liste.txt",
        ),
        buffer,
        0,
    )

    assert buffer[0].path == "Documents/token liste.txt"
    assert buffer[0].message == "Copied (new)"
