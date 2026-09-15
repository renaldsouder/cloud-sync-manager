import { useCallback, useEffect, useMemo, useState } from "react";

import BlockedRun from "./BlockedRun";
import BisyncPanel from "./BisyncPanel";
import DeleteTask from "./DeleteTask";
import RunDetail from "./RunDetail";
import TaskEditor from "./TaskEditor";
import ScheduleEditor from "./ScheduleEditor";
import TaskWizard from "./TaskWizard";
import {
  deleteTask,
  fetchRuns,
  fetchTasks,
  formatBytes,
  formatDate,
  runTask,
  stopRun,
  subscribeToRuns,
  type LiveRun,
  type Run,
  type Task,
} from "./api";

const STATUS_LABEL: Record<string, string> = {
  ready: "Prête",
  running: "En cours",
  success: "Réussie",
  warning: "Avertissement",
  error: "Erreur",
  blocked: "Bloquée — validation nécessaire",
  interrupted: "Interrompue",
  needs_resync: "Ré-initialisation nécessaire",
};

const DIRECTION_LABEL: Record<Task["direction"], string> = {
  local_to_remote: "Local → Cloud",
  remote_to_local: "Cloud → Local",
};

const MODE_LABEL: Record<Task["mode"], string> = {
  copy: "Copie",
  mirror: "Miroir",
  bisync: "Bidirectionnel",
};

/**
 * Nombre de tâches à partir duquel la barre d'outils apparaît (UI-005).
 *
 * En dessous, tout tient à l'écran : un filtre serait un encombrement, et
 * surtout un risque — laissé actif par mégarde, il fait croire qu'une tâche
 * a disparu.
 */
const SEUIL_OUTILS = 5;

type Tri = "attention" | "nom" | "prochaine";

/** Ordre du tri « ce qui demande une action d'abord ». */
const GRAVITE: Record<string, number> = {
  blocked: 0,
  needs_resync: 1,
  error: 2,
  warning: 3,
  interrupted: 4,
  running: 5,
  ready: 6,
  success: 7,
};

const ETATS: { value: string; label: string; statuts: string[] }[] = [
  { value: "tous", label: "Tous les états", statuts: [] },
  {
    value: "action",
    label: "Demandent une action",
    statuts: ["blocked", "needs_resync", "error"],
  },
  {
    value: "avertissement",
    label: "Avertissements",
    statuts: ["warning", "interrupted"],
  },
  { value: "encours", label: "En cours", statuts: ["running"] },
  { value: "reussi", label: "Réussies", statuts: ["success"] },
  { value: "attente", label: "Jamais exécutées", statuts: ["ready"] },
];

/** Recherche insensible à la casse **et** aux accents : personne ne tape
 * « Été » pour retrouver « ete », et l'inverse est tout aussi vrai. */
function replie(valeur: string): string {
  return valeur
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

function correspond(task: Task, recherche: string): boolean {
  if (!recherche) return true;
  const cible = replie(
    `${task.name} ${task.local_path} ${task.remote_path}`,
  );
  return replie(recherche)
    .split(/\s+/)
    .filter(Boolean)
    .every((mot) => cible.includes(mot));
}

type Props = {
  bidirectional?: boolean;
};

export default function TasksView({ bidirectional = false }: Props) {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [live, setLive] = useState<Record<string, LiveRun>>({});
  const [runs, setRuns] = useState<Record<string, Run[]>>({});
  const [detail, setDetail] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [planning, setPlanning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Task | null>(null);
  const [duplicating, setDuplicating] = useState<Task | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  // UI-005 — volontairement non mémorisé d'une visite à l'autre : un filtre
  // retrouvé actif sans qu'on l'ait posé fait chercher une tâche qui est
  // pourtant là.
  const [recherche, setRecherche] = useState("");
  const [etat, setEtat] = useState("tous");
  const [tri, setTri] = useState<Tri>("attention");

  const reload = useCallback(async () => {
    try {
      setTasks(await fetchTasks());
      setError(null);
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Progression temps réel. Quand une exécution disparaît du flux, elle est
  // terminée : on rafraîchit pour récupérer son résultat définitif.
  useEffect(() => {
    let previous = 0;
    return subscribeToRuns((incoming) => {
      setLive(Object.fromEntries(incoming.map((run) => [run.task_id, run])));
      if (previous > 0 && incoming.length < previous) {
        void reload();
      }
      previous = incoming.length;
    });
  }, [reload]);

  async function act(action: () => Promise<unknown>) {
    try {
      await action();
      await reload();
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  const filtrant = recherche.trim() !== "" || etat !== "tous";

  const visibles = useMemo(() => {
    if (!tasks) return [];
    const statuts = ETATS.find((e) => e.value === etat)?.statuts ?? [];
    const retenues = tasks.filter(
      (task) =>
        correspond(task, recherche.trim()) &&
        (statuts.length === 0 || statuts.includes(task.status)),
    );

    const parNom = (a: Task, b: Task) => a.name.localeCompare(b.name, "fr");
    return [...retenues].sort((a, b) => {
      if (tri === "nom") return parNom(a, b);
      if (tri === "prochaine") {
        // Une tâche sans planification n'a pas de prochaine exécution : elle
        // passe après toutes celles qui en ont une, pas avant.
        const sansDate = Number.MAX_SAFE_INTEGER;
        const da = a.next_run_at ? Date.parse(a.next_run_at) : sansDate;
        const db = b.next_run_at ? Date.parse(b.next_run_at) : sansDate;
        return da === db ? parNom(a, b) : da - db;
      }
      const ga = GRAVITE[a.status] ?? 9;
      const gb = GRAVITE[b.status] ?? 9;
      return ga === gb ? parNom(a, b) : ga - gb;
    });
  }, [tasks, recherche, etat, tri]);

  function toutAfficher() {
    setRecherche("");
    setEtat("tous");
  }

  async function toggleHistory(task: Task) {
    if (expanded === task.id) {
      setExpanded(null);
      setDetail(null);
      return;
    }
    setExpanded(task.id);
    setDetail(null);
    try {
      setRuns((previous) => ({ ...previous, [task.id]: [] }));
      const history = await fetchRuns(task.id);
      setRuns((previous) => ({ ...previous, [task.id]: history }));
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  if (editing) {
    return (
      <TaskEditor
        task={editing}
        onCancel={() => setEditing(null)}
        onSaved={() => {
          setEditing(null);
          void reload();
        }}
      />
    );
  }

  if (adding || duplicating) {
    return (
      <TaskWizard
        bidirectional={bidirectional}
        template={duplicating}
        onCancel={() => {
          setAdding(false);
          setDuplicating(null);
        }}
        onCreated={() => {
          setAdding(false);
          setDuplicating(null);
          void reload();
        }}
      />
    );
  }

  return (
    <section className="card">
      <div className="card__head">
        <h2>Tâches de synchronisation</h2>
        <button type="button" className="btn" onClick={() => setAdding(true)}>
          Nouvelle tâche
        </button>
      </div>

      {error && <p className="state state--error">{error}</p>}
      {!tasks && !error && <p className="state">Chargement…</p>}
      {tasks?.length === 0 && (
        <p className="state">
          Aucune tâche. Créez-en une après avoir ajouté et testé un stockage Cloud.
        </p>
      )}

      {tasks && tasks.length >= SEUIL_OUTILS && (
        <div className="outils">
          <label className="field outils__recherche">
            <span className="field__label">Rechercher</span>
            <input
              value={recherche}
              placeholder="nom, dossier local ou distant"
              onChange={(event) => setRecherche(event.target.value)}
            />
          </label>

          <label className="field">
            <span className="field__label">État</span>
            <select value={etat} onChange={(event) => setEtat(event.target.value)}>
              {ETATS.map((choix) => (
                <option key={choix.value} value={choix.value}>
                  {choix.label}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span className="field__label">Trier par</span>
            <select
              value={tri}
              onChange={(event) => setTri(event.target.value as Tri)}
            >
              <option value="attention">Ce qui demande une action</option>
              <option value="nom">Nom</option>
              <option value="prochaine">Prochaine exécution</option>
            </select>
          </label>
        </div>
      )}

      {tasks && filtrant && (
        // Sans ce rappel, une tâche masquée par un filtre oublié passe pour
        // une tâche supprimée.
        <p className="state">
          {visibles.length === 0
            ? "Aucune tâche ne correspond."
            : `${visibles.length} tâche(s) affichée(s) sur ${tasks.length}.`}{" "}
          <button type="button" className="chip chip--button" onClick={toutAfficher}>
            Tout afficher
          </button>
        </p>
      )}

      <ul className="remotes">
        {visibles.map((task) => {
          const running = live[task.id];
          const history = runs[task.id];
          return (
            <li key={task.id} className="remote">
              <div className="remote__head">
                <span
                  className={`dot dot--${running ? "running" : task.status}`}
                  aria-hidden="true"
                />
                <span className="remote__name">{task.name}</span>
                <span className="remote__provider">{DIRECTION_LABEL[task.direction]}</span>
                <span className="remote__provider">{MODE_LABEL[task.mode]}</span>
                <span className="remote__status">
                  {running
                    ? running.dry_run
                      ? "Simulation en cours"
                      : running.phase === "transfert"
                        ? "En cours"
                        : running.phase
                    : (STATUS_LABEL[task.status] ?? task.status)}
                </span>
              </div>

              <p className="path">
                <span className="chip">{task.schedule_label}</span>
                {task.next_run_at && (
                  <span className="chip">prochaine : {formatDate(task.next_run_at)}</span>
                )}
              </p>

              <p className="path">
                {task.direction === "local_to_remote" ? (
                  <>
                    <code>{task.local_path}</code> → <code>{task.remote_path || "racine"}</code>
                  </>
                ) : (
                  <>
                    <code>{task.remote_path || "racine"}</code> → <code>{task.local_path}</code>
                  </>
                )}
              </p>

              {planning === task.id && (
                <ScheduleEditor
                  task={task}
                  onCancel={() => setPlanning(null)}
                  onSaved={() => {
                    setPlanning(null);
                    void reload();
                  }}
                />
              )}

              {running && <Progress run={running} />}

              {deleting === task.id && (
                <DeleteTask
                  task={task}
                  busy={false}
                  onCancel={() => setDeleting(null)}
                  onConfirm={() => {
                    setDeleting(null);
                    void act(() => deleteTask(task.id));
                  }}
                />
              )}

              {task.mode === "bisync" && !running && (
                <BisyncPanel
                  taskId={task.id}
                  taskName={task.name}
                  busy={Boolean(running)}
                  onSimulateResync={() =>
                    void act(() => runTask(task.id, true, false, true))
                  }
                  onApplyResync={() => {
                    if (
                      window.confirm(
                        `Fusionner les deux côtés de « ${task.name} » ? ` +
                          "Ce qui n'existe que d'un bord sera copié vers l'autre.",
                      )
                    ) {
                      void act(() => runTask(task.id, false, false, true));
                    }
                  }}
                />
              )}

              {!running && task.status === "blocked" && task.last_run_id && (
                <BlockedRun
                  runId={task.last_run_id}
                  taskName={task.name}
                  onConfirm={() => void act(() => runTask(task.id, false, true))}
                />
              )}

              {expanded === task.id && (
                <ul className="history">
                  {history?.length === 0 && <li className="state">Aucune exécution.</li>}
                  {history?.map((run) => (
                    <li key={run.id}>
                      <button
                        type="button"
                        className="history__row"
                        aria-expanded={detail === run.id}
                        onClick={() => setDetail(detail === run.id ? null : run.id)}
                      >
                        <span className={`dot dot--${run.status}`} aria-hidden="true" />
                        {new Date(run.started_at).toLocaleString("fr-FR")} ·{" "}
                        {STATUS_LABEL[run.status] ?? run.status}
                        {run.dry_run && " (simulation)"} · {run.transferred_files} fichier(s) ·{" "}
                        {formatBytes(run.transferred_bytes)}
                        {run.deleted_files > 0 && ` · ${run.deleted_files} supprimé(s)`}
                        {run.errors_count > 0 && ` · ${run.errors_count} erreur(s)`}
                      </button>
                      {detail === run.id && (
                        <RunDetail runId={run.id} onClose={() => setDetail(null)} />
                      )}
                    </li>
                  ))}
                </ul>
              )}

              <div className="actions">
                {running ? (
                  <button
                    type="button"
                    className="btn btn--small"
                    onClick={() => void act(() => stopRun(running.run_id))}
                  >
                    Arrêter
                  </button>
                ) : (
                  <>
                    <button
                      type="button"
                      className="btn btn--small btn--ghost"
                      onClick={() => void act(() => runTask(task.id, true))}
                    >
                      Simuler
                    </button>
                    <button
                      type="button"
                      className="btn btn--small"
                      onClick={() => void act(() => runTask(task.id, false))}
                    >
                      Lancer
                    </button>
                  </>
                )}
                {!running && (
                  <button
                    type="button"
                    className="btn btn--small btn--ghost"
                    onClick={() => setPlanning(planning === task.id ? null : task.id)}
                  >
                    {planning === task.id ? "Fermer" : "Planifier"}
                  </button>
                )}
                {!running && (
                  <button
                    type="button"
                    className="btn btn--small btn--ghost"
                    onClick={() => setEditing(task)}
                  >
                    Modifier
                  </button>
                )}
                {!running && (
                  <button
                    type="button"
                    className="btn btn--small btn--ghost"
                    onClick={() => setDuplicating(task)}
                  >
                    Dupliquer
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn--small btn--ghost"
                  onClick={() => void toggleHistory(task)}
                >
                  {expanded === task.id ? "Masquer l'historique" : "Historique"}
                </button>
                {!running && (
                  <button
                    type="button"
                    className="btn btn--small btn--ghost"
                    onClick={() => {
                      if (task.mode === "copy") {
                        // Une Copie ne supprime rien à distance : exiger la
                        // confirmation renforcée partout la banaliserait là
                        // où elle compte (§10.4).
                        if (
                          window.confirm(`Supprimer la tâche « ${task.name} » ?`)
                        ) {
                          void act(() => deleteTask(task.id));
                        }
                        return;
                      }
                      setDeleting(deleting === task.id ? null : task.id);
                    }}
                  >
                    Supprimer
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Progress({ run }: { run: LiveRun }) {
  const done = run.stats.bytes ?? 0;
  const total = run.stats.total_bytes ?? 0;
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : null;

  return (
    <div className="progress">
      <div className="progress__bar">
        <div
          className="progress__fill"
          style={{ width: percent === null ? "100%" : `${percent}%` }}
        />
      </div>
      <p className="progress__detail">
        {run.phase !== "transfert" && <>{run.phase} · </>}
        {percent !== null && <>{percent} % · </>}
        {formatBytes(done)}
        {total > 0 && <> / {formatBytes(total)}</>}
        {run.stats.speed ? <> · {formatBytes(run.stats.speed)}/s</> : null}
        {run.counters.transfers > 0 && <> · {run.counters.transfers} fichier(s)</>}
        {run.counters.errors > 0 && <> · {run.counters.errors} erreur(s)</>}
      </p>
      {run.current_file && <p className="progress__file">{run.current_file}</p>}
      {run.last_error && <p className="state state--error">{run.last_error}</p>}
    </div>
  );
}
