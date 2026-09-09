import { useEffect, useState } from "react";

import { fetchHealth, type Health } from "./api";

const LABELS: Record<string, string> = {
  database: "Base de configuration",
  rclone: "Moteur rclone",
};

export default function App() {
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
      </header>

      <section className="card">
        <h2>État du service</h2>

        {error && <p className="state state--error">Contact impossible avec l'API : {error}</p>}

        {!health && !error && <p className="state">Interrogation en cours…</p>}

        {health && (
          <ul className="checks">
            {Object.entries(health.checks).map(([key, check]) => (
              <li key={key} className="checks__item">
                <span
                  className={`dot ${check.ok ? "dot--ok" : "dot--ko"}`}
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

      <footer className="shell__footer">
        Fondations (J1) : API, base de configuration, conteneur. Les stockages
        Cloud et les tâches arrivent aux étapes suivantes.
      </footer>
    </main>
  );
}
