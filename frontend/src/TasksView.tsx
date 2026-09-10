import { useCallback, useEffect, useState } from "react";

import BlockedRun from "./BlockedRun";
import RunDetail from "./RunDetail";
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

export default function TasksView() {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [live, setLive] = useState<Record<string, LiveRun>>({});
  const [runs, setRuns] = useState<Record<string, Run[]>>({});
  const [detail, setDetail] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [planning, setPlanning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

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

  if (adding) {
    return (
      <TaskWizard
        onCancel={() => setAdding(false)}
        onCreated={() => {
          setAdding(false);
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

      <ul className="remotes">
        {tasks?.map((task) => {
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
                      if (window.confirm(`Supprimer la tâche « ${task.name} » ?`)) {
                        void act(() => deleteTask(task.id));
                      }
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
