import { useEffect, useState } from "react";

import RemotesView from "./RemotesView";
import TasksView from "./TasksView";
import { fetchHealth, type Health } from "./api";

const LABELS: Record<string, string> = {
  database: "Base de configuration",
  rclone: "Moteur rclone",
};

type Tab = "taches" | "stockages" | "etat";

export default function App() {
  const [tab, setTab] = useState<Tab>("taches");
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchHealth(controller.signal)
      .then((data) => {
        setHealth(data);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      });
    return () => controller.abort();
  }, []);

  return (
    <main className="shell">
      <header className="shell__header">
        <h1>Cloud Sync Manager</h1>
        <p className="shell__subtitle">
          Synchronisation Cloud pour Unraid{health ? ` — version ${health.version}` : ""}
        </p>
        <nav className="tabs">
          {(
            [
              ["taches", "Tâches"],
              ["stockages", "Stockages Cloud"],
              ["etat", "État"],
            ] as [Tab, string][]
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={tab === value ? "tab tab--active" : "tab"}
              onClick={() => setTab(value)}
            >
              {label}
            </button>
          ))}
        </nav>
      </header>

      {tab === "taches" ? (
        <TasksView />
      ) : tab === "stockages" ? (
        <RemotesView />
      ) : (
        <section className="card">
          <h2>État du service</h2>

          {error && (
            <p className="state state--error">Contact impossible avec l'API : {error}</p>
          )}
          {!health && !error && <p className="state">Interrogation en cours…</p>}

          {health && (
            <ul className="checks">
              {Object.entries(health.checks).map(([key, check]) => (
                <li key={key} className="checks__item">
                  <span
                    className={`dot ${check.ok ? "dot--ok" : "dot--error"}`}
                    aria-hidden="true"
                  />
                  <span className="checks__label">{LABELS[key] ?? key}</span>
                  <span className="checks__value">
                    {check.ok
                      ? (check.version ?? "disponible")
                      : (check.detail ?? "indisponible")}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      <footer className="shell__footer">
        Copie Local ↔ Cloud, simulation, progression et arrêt (J3). Le Miroir et
        ses protections contre les suppressions arrivent à l'étape suivante.
      </footer>
    </main>
  );
}
