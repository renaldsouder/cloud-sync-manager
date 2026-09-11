"""Matrice destructive du bidirectionnel (§20.3, §8.2, §8.5, §8.6).

Le §20.3 énumère ce qui doit être éprouvé avant toute version contenant un
mode destructif. Ce fichier couvre ces scénarios pour bisync, qui en ajoute
un de son cru : le verrou laissé par une exécution tuée en vol, lequel bloque
la tâche pour toujours si personne ne le lève.

Chaque cas vérifie ce que subissent les fichiers, pas seulement le statut
renvoyé. Une tâche « bloquée » qui aurait tout de même supprimé serait un
échec, et le statut seul ne le dirait pas.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csm.db.models import Task, TaskRun
from csm.services.runner import (
    clear_stale_bisync_locks,
    mark_orphan_runs_interrupted,
)
def configure(client: TestClient, task_id: str, **champs: object) -> None:
    """Règle les seuils directement en base.

    ``TaskUpdate`` ne les expose pas, et un PATCH portant ces champs serait
    accepté puis ignoré : le test passerait alors pour de mauvaises raisons.
    """
    session = client.app.state.session_factory()
    try:
        task = session.get(Task, task_id)
        for cle, valeur in champs.items():
            setattr(task, cle, valeur)
        session.commit()
    finally:
        session.close()


from tests.test_bisync_protocol import (  # noqa: E402,F401 — fixture réutilisée
    make_bisync_task,
    ouvert,
    run,
    settings_of,
    wait,
)

pytestmark = pytest.mark.usefixtures("rclone_path")


def initialise(client: TestClient, task_id: str) -> None:
    """Amène une tâche à l'état « référence établie »."""
    wait(client, run(client, task_id, resync=True).json()["id"])
    wait(client, run(client, task_id, dry_run=False, resync=True).json()["id"])
    assert settings_of(client, task_id)["initialised"] is True


def peupler(dossier: Path, nombre: int, prefixe: str = "f") -> None:
    dossier.mkdir(parents=True, exist_ok=True)
    for index in range(nombre):
        (dossier / f"{prefixe}{index}.txt").write_text(f"c{index}", encoding="utf-8")


def noms(dossier: Path) -> set[str]:
    return {f.name for f in dossier.iterdir() if f.is_file()}


# -- §8.2 : source vide, côté démonté, côté inaccessible ----------------------


def test_an_emptied_side_never_empties_the_other(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Le scénario du partage démonté, côté local."""
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    peupler(local, 12)
    initialise(ouvert, task_id)

    for fichier in local.iterdir():
        fichier.unlink()

    final = wait(ouvert, run(ouvert, task_id, dry_run=False).json()["id"])
    assert final["status"] == "blocked", final
    assert len(noms(cloud)) == 12, "rien ne doit avoir été supprimé à distance"


def test_an_emptied_remote_side_never_empties_the_local(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Le même scénario dans l'autre sens.

    En bidirectionnel, un contrôle à sens unique laisserait passer la moitié
    des cas : c'est la destination qui deviendrait le déclencheur.
    """
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    peupler(local, 12)
    initialise(ouvert, task_id)

    for fichier in cloud.iterdir():
        fichier.unlink()

    final = wait(ouvert, run(ouvert, task_id, dry_run=False).json()["id"])
    assert final["status"] == "blocked", final
    assert len(noms(local)) == 12, "rien ne doit avoir été supprimé localement"


def test_an_unreachable_side_stops_everything(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    peupler(local, 12)
    initialise(ouvert, task_id)

    # Le dossier distant disparaît entièrement : ce n'est plus « vide »,
    # c'est injoignable.
    for fichier in cloud.iterdir():
        fichier.unlink()
    cloud.rmdir()

    final = wait(ouvert, run(ouvert, task_id, dry_run=False).json()["id"])
    assert final["status"] in {"blocked", "error"}, final
    assert len(noms(local)) == 12


# -- §8.3 : volumes de suppression et seuil -----------------------------------


@pytest.mark.parametrize("supprimes", [1, 2, 12])
def test_deletions_below_the_threshold_propagate(
    ouvert: TestClient,
    tmp_path: Path,
    local_root: Path,
    supprimes: int,
) -> None:
    """Un fichier, 10 %, 100 % — le seuil décide, pas le hasard.

    Le seuil est ici volontairement haut : on éprouve la propagation, le cas
    du blocage a son propre test.
    """
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    peupler(local, 12)
    initialise(ouvert, task_id)

    configure(ouvert, task_id, max_deletes=100, max_delete_percent=100)
    for fichier in sorted(local.iterdir())[:supprimes]:
        fichier.unlink()

    final = wait(ouvert, run(ouvert, task_id, dry_run=False).json()["id"])
    assert final["status"] in {"success", "warning", "blocked"}, final
    if final["status"] != "blocked":
        assert len(noms(cloud)) == 12 - supprimes


def test_crossing_the_threshold_blocks_without_deleting(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """§8.3 — au-delà du seuil, la tâche s'arrête et ne détruit rien."""
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    peupler(local, 12)
    initialise(ouvert, task_id)

    configure(ouvert, task_id, max_deletes=2, max_delete_percent=100)
    for fichier in sorted(local.iterdir())[:6]:
        fichier.unlink()

    final = wait(ouvert, run(ouvert, task_id, dry_run=False).json()["id"])
    assert final["status"] == "blocked", final
    assert len(noms(cloud)) == 12, "aucune suppression avant validation"
    assert final["summary"]["blocked_reason"]


def test_the_blocked_run_names_the_files(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """§10.4 — montrer *quels* fichiers, pas seulement combien."""
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    peupler(local, 12)
    initialise(ouvert, task_id)

    configure(ouvert, task_id, max_deletes=1, max_delete_percent=100)
    condamnes = {f.name for f in sorted(local.iterdir())[:4]}
    for fichier in sorted(local.iterdir())[:4]:
        fichier.unlink()

    final = wait(ouvert, run(ouvert, task_id, dry_run=False).json()["id"])
    assert final["status"] == "blocked"

    prevus = ouvert.get(
        f"/api/runs/{final['id']}/events", params={"kind": "skip_delete"}
    ).json()
    assert {event["path"] for event in prevus} == condamnes


# -- §20.3 : la simulation doit correspondre à l'exécution --------------------


def test_the_simulation_matches_what_the_run_does(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    peupler(local, 12)
    initialise(ouvert, task_id)

    (local / "neuf.txt").write_text("neuf", encoding="utf-8")
    simule = wait(ouvert, run(ouvert, task_id).json()["id"])
    assert simule["status"] == "success"
    assert not (cloud / "neuf.txt").exists(), "une simulation n'écrit rien"

    annonce = {
        event["path"]
        for event in ouvert.get(f"/api/runs/{simule['id']}/events").json()
        if event["kind"] in {"transfer", "skip_transfer"} and event["path"]
    }
    assert "neuf.txt" in annonce

    reel = wait(ouvert, run(ouvert, task_id, dry_run=False).json()["id"])
    assert reel["status"] in {"success", "warning"}
    assert (cloud / "neuf.txt").exists(), "ce qui était annoncé doit arriver"


# -- §8.5 : arrêt, plantage, redémarrage --------------------------------------


def test_a_stopped_run_is_never_reported_as_successful(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """§8.5 — après un arrêt forcé, « Interrompue », jamais « Réussie ».

    L'exécution est volontairement bridée : sans cela, elle se termine en
    quelques centaines de millisecondes sur une machine rapide, l'arrêt
    arrive après coup, et le test se contente de constater une réussite —
    il passerait alors sans jamais avoir éprouvé ce qu'il prétend.
    """
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    peupler(local, 300, prefixe="gros")
    for fichier in local.iterdir():
        fichier.write_text("x" * 20_000, encoding="utf-8")

    wait(ouvert, run(ouvert, task_id, resync=True).json()["id"])
    configure(ouvert, task_id, bandwidth_json=json.dumps({"limit": "8k"}))

    started = run(ouvert, task_id, dry_run=False, resync=True).json()

    # On attend que le transfert ait réellement commencé, plutôt que de
    # parier sur un délai.
    debut = time.monotonic()
    while time.monotonic() - debut < 20:
        if ouvert.get(f"/api/runs/{started['id']}").json()["status"] != "running":
            break
        arret = ouvert.post(f"/api/runs/{started['id']}/stop")
        if arret.status_code < 400:
            break
        time.sleep(0.05)

    final = wait(ouvert, started["id"])
    assert final["status"] == "interrupted", final


def test_a_crash_leaves_a_lock_that_startup_lifts(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Le piège propre à bisync.

    Il pose un ``.lck`` et refuse de démarrer tant qu'il existe. Après un arrêt
    brutal du conteneur, le verrou survit : sans levée au démarrage, la tâche
    resterait bloquée pour toujours et le seul remède documenté serait de
    supprimer le fichier à la main.
    """
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    peupler(local, 12)
    initialise(ouvert, task_id)

    workdir = tmp_path / "config" / "bisync" / task_id
    verrou = workdir / "simule.lck"
    verrou.write_text("verrou d'une exécution morte", encoding="utf-8")

    # Une exécution restée « en cours », comme après un arrêt brutal.
    session = ouvert.app.state.session_factory()
    try:
        session.add(TaskRun(task_id=task_id, status="running", dry_run=False))
        session.get(Task, task_id).status = "running"
        session.commit()
    finally:
        session.close()

    assert mark_orphan_runs_interrupted(
        ouvert.app.state.session_factory, tmp_path / "config" / "bisync"
    ) == 1
    assert not verrou.exists(), "le verrou doit être levé au démarrage"

    final = wait(ouvert, run(ouvert, task_id, dry_run=False).json()["id"])
    assert final["status"] in {"success", "warning", "needs_resync"}, final


def test_locks_are_only_lifted_for_the_named_tasks(tmp_path: Path) -> None:
    """La levée est chirurgicale : forcer un verrou au hasard rouvrirait la
    porte à deux exécutions concurrentes sur la même paire."""
    racine = tmp_path / "bisync"
    (racine / "morte").mkdir(parents=True)
    (racine / "vivante").mkdir(parents=True)
    (racine / "morte" / "a.lck").write_text("x", encoding="utf-8")
    (racine / "vivante" / "b.lck").write_text("x", encoding="utf-8")

    assert clear_stale_bisync_locks(["morte"], racine) == 1
    assert not (racine / "morte" / "a.lck").exists()
    assert (racine / "vivante" / "b.lck").exists()


def test_listings_are_left_alone_when_a_lock_is_lifted(tmp_path: Path) -> None:
    """Effacer un listing forcerait une fusion : seul le verrou doit partir."""
    racine = tmp_path / "bisync"
    (racine / "t").mkdir(parents=True)
    (racine / "t" / "a.lck").write_text("x", encoding="utf-8")
    (racine / "t" / "path1.lst").write_text("listing", encoding="utf-8")

    clear_stale_bisync_locks(["t"], racine)
    assert (racine / "t" / "path1.lst").exists()


def test_lifting_locks_without_a_directory_is_harmless(tmp_path: Path) -> None:
    assert clear_stale_bisync_locks(["inexistante"], tmp_path / "absent") == 0
    assert clear_stale_bisync_locks(["t"], None) == 0


# -- §8.6 : renommages, suppressions croisées, casse et Unicode ---------------


def test_a_rename_travels_as_a_rename(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    peupler(local, 12)
    initialise(ouvert, task_id)

    (local / "f0.txt").rename(local / "renomme.txt")
    final = wait(ouvert, run(ouvert, task_id, dry_run=False).json()["id"])
    assert final["status"] in {"success", "warning", "blocked"}, final

    if final["status"] != "blocked":
        assert "renomme.txt" in noms(cloud)
        assert "f0.txt" not in noms(cloud)


def test_crossed_deletions_are_not_a_conflict(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Deux suppressions distinctes, une de chaque côté : rien ne s'oppose."""
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    peupler(local, 12)
    initialise(ouvert, task_id)

    configure(ouvert, task_id, max_deletes=100, max_delete_percent=100)
    (local / "f0.txt").unlink()
    (cloud / "f1.txt").unlink()

    final = wait(ouvert, run(ouvert, task_id, dry_run=False).json()["id"])
    assert final["status"] in {"success", "warning"}, final
    assert "f0.txt" not in noms(cloud)
    assert "f1.txt" not in noms(local)


def test_accents_survive_the_round_trip(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """GLOBAL-005 — un nom accentué doit traverser intact, et être lisible
    dans l'historique."""
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    peupler(local, 12)
    initialise(ouvert, task_id)

    (cloud / "été à Noël.txt").write_text("accents", encoding="utf-8")
    final = wait(ouvert, run(ouvert, task_id, dry_run=False).json()["id"])
    assert final["status"] in {"success", "warning"}, final
    assert "été à Noël.txt" in noms(local)

    chemins = {
        event["path"]
        for event in ouvert.get(f"/api/runs/{final['id']}/events").json()
        if event["path"]
    }
    assert "été à Noël.txt" in chemins


def test_case_differences_do_not_erase_a_file(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """§8.6 — les différences de casse ne doivent pas faire disparaître un
    fichier, quel que soit le comportement du système de fichiers."""
    local, cloud = local_root / "local", tmp_path / "cloud"
    task_id = make_bisync_task(ouvert, local, cloud)
    peupler(local, 12)
    initialise(ouvert, task_id)

    (cloud / "Rapport.txt").write_text("majuscule", encoding="utf-8")
    final = wait(ouvert, run(ouvert, task_id, dry_run=False).json()["id"])
    assert final["status"] in {"success", "warning"}, final

    presents = {nom.lower() for nom in noms(local)}
    assert "rapport.txt" in presents
    assert len(noms(local)) >= 13, "aucun fichier ne doit avoir été perdu"


# -- le contournement reste cantonné à la mesure ------------------------------


def test_force_never_reaches_a_real_run(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """``--force`` lève la garde interne de bisync.

    Il est indispensable pour mesurer jusqu'au bout — une simulation n'écrit
    rien — et interdit partout ailleurs : dans une exécution réelle, il
    supprimerait sans limite.
    """
    from csm.services.runner import RunError, RunPlan

    runner = ouvert.app.state.run_manager
    plan = RunPlan(
        operation="bisync",
        source=str(local_root / "a"),
        destination=str(tmp_path / "b"),
        dry_run=False,
        workdir=str(tmp_path / "work"),
    )

    with pytest.raises(RunError, match="contournement"):
        runner._bisync_arguments(plan, extra=["--force"])

    mesure = runner._bisync_arguments(
        RunPlan(**{**plan.__dict__, "dry_run": True}), extra=["--force"]
    )
    assert "--force" in mesure and "--dry-run" in mesure


def test_the_measurement_is_not_capped_by_our_own_threshold(
    ouvert: TestClient, tmp_path: Path, local_root: Path
) -> None:
    """Transmettre le seuil à la mesure la ferait avorter sur la limite
    qu'elle sert justement à évaluer."""
    from csm.services.runner import RunPlan

    runner = ouvert.app.state.run_manager
    plan = RunPlan(
        operation="bisync",
        source=str(local_root / "a"),
        destination=str(tmp_path / "b"),
        dry_run=False,
        workdir=str(tmp_path / "work"),
        max_deletes=1,
    )

    mesure = runner._simulation_arguments(plan)
    assert "--max-delete" not in mesure
    assert "--force" in mesure

    reel = runner._arguments(None, plan)  # type: ignore[arg-type]
    assert reel[reel.index("--max-delete") + 1] == "1"
    assert "--force" not in reel
