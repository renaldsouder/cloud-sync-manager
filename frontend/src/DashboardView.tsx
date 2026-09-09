import { useCallback, useEffect, useState } from "react";

import {
  fetchDashboard,
  formatBytes,
  formatDate,
  subscribeToRuns,
  type Dashboard,
} from "./api";

const CARDS: [keyof Dashboard["counters"], string][] = [
  ["total", "Tâches"],
  ["scheduled", "Planifiées"],
  ["running", "En cours"],
  ["warning", "Avertissements"],
  ["error", "En erreur"],
  ["blocked", "Bloquées"],
  ["paused", "En pause"],
];

const RUN_LABEL: Record<string, string> = {
  success: "Réussie",
  warning: "Avertissement",
  error: "Erreur",
  blocked: "Bloquée",
  interrupted: "Interrompue",
  running: "En cours",
};

/** Tableau de bord (UI-001, UI-004, §10.2) : tout l'état en un seul appel. */
export default function DashboardView() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setData(await fetchDashboard());
      setError(null);
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Le flux de progression sert aussi de battement : dès qu'une exécution
  // démarre ou s'achève, les compteurs sont rafraîchis.
  useEffect(() => {
    let previous = -1;
    return subscribeToRuns((incoming) => {
      if (incoming.length !== previous) {
        previous = incoming.length;
        void reload();
      }
    });
  }, [reload]);

  if (error) return <p className="state state--error">{error}</p>;
  if (!data) return <p className="state">Chargement…</p>;

  const { counters } = data;

  return (
    <>
      <section className="cards">
        {CARDS.filter(([key]) => key === "total" || counters[key] > 0).map(
          ([key, label]) => (
            <div className="tile" key={key}>
              <span className="tile__value">{counters[key]}</span>
              <span className="tile__label">{label}</span>
            </div>
          ),
        )}
      </section>

      <section className="card">
        <h2>Activité</h2>
        <dl className="facts">
          <div>
            <dt>Prochaine exécution</dt>
            <dd>{formatDate(data.next_run_at)}</dd>
          </div>
          <div>
            <dt>Débit cumulé</dt>
            <dd>
              {data.throughput_bytes_per_second > 0
                ? `${formatBytes(data.throughput_bytes_per_second)}/s`
                : "—"}
            </dd>
          </div>
        </dl>

        {data.running.length > 0 && (
          <ul className="history">
            {data.running.map((run) => (
              <li key={run.run_id}>
                <span className="dot dot--running" aria-hidden="true" />
                {run.task_name} · {run.dry_run ? "simulation" : run.phase} ·{" "}
                {formatBytes(run.stats.bytes ?? 0)}
                {run.current_file && ` · ${run.current_file}`}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card">
        <h2>Dernières exécutions</h2>
        {data.recent_runs.length === 0 ? (
          <p className="state">Aucune exécution pour l'instant.</p>
        ) : (
          <ul className="history">
            {data.recent_runs.map((run) => (
              <li key={run.id}>
                <span className={`dot dot--${run.status}`} aria-hidden="true" />
                {formatDate(run.started_at)} · {RUN_LABEL[run.status] ?? run.status}
                {run.dry_run && " (simulation)"} · {run.transferred_files} fichier(s) ·{" "}
                {formatBytes(run.transferred_bytes)}
                {run.deleted_files > 0 && ` · ${run.deleted_files} supprimé(s)`}
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
