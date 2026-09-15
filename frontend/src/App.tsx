import { useCallback, useEffect, useState } from "react";

import DashboardView from "./DashboardView";
import HelpView from "./HelpView";
import LoginView from "./LoginView";
import RemotesView from "./RemotesView";
import SettingsView from "./SettingsView";
import TasksView from "./TasksView";
import WelcomeView from "./WelcomeView";
import {
  dismissOnboarding,
  fetchAuthSession,
  fetchHealth,
  fetchOnboarding,
  logout,
  type AuthState,
  type Health,
  type Onboarding,
} from "./api";

const LABELS: Record<string, string> = {
  database: "Base de configuration",
  rclone: "Moteur rclone",
};

type Tab = "bord" | "taches" | "stockages" | "parametres" | "aide" | "etat";

export default function App() {
  const [tab, setTab] = useState<Tab>("bord");
  const [health, setHealth] = useState<Health | null>(null);
  const [accueil, setAccueil] = useState<Onboarding | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [auth, setAuth] = useState<AuthState | null>(null);

  const refreshAuth = useCallback(async () => {
    try {
      setAuth(await fetchAuthSession());
    } catch {
      // L'écran d'état signalera l'API injoignable ; ne pas verrouiller
      // l'interface sur une erreur réseau passagère.
      setAuth({ enabled: false, authenticated: true });
    }
  }, []);

  // Relu à chaque changement d'onglet : poser un mot de passe depuis les
  // paramètres doit faire disparaître l'avertissement sans recharger la page.
  useEffect(() => {
    void refreshAuth();
  }, [refreshAuth, tab]);

  // Relu au changement d'onglet : une étape franchie ailleurs doit se
  // refléter en revenant au tableau de bord, sans recharger la page.
  useEffect(() => {
    const controller = new AbortController();
    fetchOnboarding(controller.signal)
      .then(setAccueil)
      .catch(() => {
        // L'assistant est une aide : son absence ne doit jamais empêcher
        // d'utiliser le reste.
      });
    return () => controller.abort();
  }, [tab]);

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

  if (auth === null) return null;
  if (auth.enabled && !auth.authenticated) {
    return <LoginView onAuthenticated={() => void refreshAuth()} />;
  }

  return (
    <main className="shell">
      <header className="shell__header">
        <h1>
          <img className="shell__logo" src="/favicon.png" alt="" />
          Cloud Sync Manager
        </h1>
        <p className="shell__subtitle">
          Synchronisation Cloud pour Unraid{health ? ` — version ${health.version}` : ""}
        </p>
        <nav className="tabs">
          {(
            [
              ["bord", "Tableau de bord"],
              ["taches", "Tâches"],
              ["stockages", "Stockages Cloud"],
              ["parametres", "Paramètres"],
              ["aide", "Aide"],
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
        {auth.enabled && (
          <button
            type="button"
            className="btn btn--small btn--ghost shell__logout"
            onClick={() => void logout().then(refreshAuth)}
          >
            Se déconnecter
          </button>
        )}
      </header>

      {!auth.enabled && tab !== "parametres" && (
        <div className="warning">
          <span>
            <strong>Cette interface n'est protégée par aucun mot de passe.</strong>{" "}
            Toute personne pouvant joindre ce port sur votre réseau peut créer une
            tâche et déclencher des suppressions.
          </span>
          <button type="button" className="btn btn--small" onClick={() => setTab("parametres")}>
            Protéger l'interface
          </button>
        </div>
      )}

      {tab === "bord" ? (
        accueil && !accueil.dismissed && !accueil.completed ? (
          <WelcomeView
            etat={accueil}
            onAller={(onglet) => setTab(onglet)}
            onIgnorer={() => void dismissOnboarding().then(setAccueil)}
          />
        ) : (
          <DashboardView />
        )
      ) : tab === "taches" ? (
        <TasksView bidirectional={health?.features?.bidirectional ?? false} />
      ) : tab === "stockages" ? (
        <RemotesView />
      ) : tab === "parametres" ? (
        <SettingsView />
      ) : tab === "aide" ? (
        <HelpView bidirectional={health?.features?.bidirectional ?? false} />
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
        Copie et Miroir dans les deux sens, protections destructives, planification,
        filtres, notifications et sauvegarde de configuration.{" "}
        {health?.features?.bidirectional
          ? "Le bidirectionnel demande une initialisation explicite avant sa première exécution."
          : "Le bidirectionnel attend la fin de ses tests destructifs : à moitié fiable, il perdrait des données."}
      </footer>
    </main>
  );
}
