import { useState } from "react";

import { login } from "./api";

/** Écran de connexion, affiché dès qu'un mot de passe est défini (SEC-006). */
export default function LoginView({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(password, remember);
      setPassword("");
      onAuthenticated();
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="shell shell--narrow">
      <header className="shell__header">
        <h1>Cloud Sync Manager</h1>
        <p className="shell__subtitle">Synchronisation Cloud pour Unraid</p>
      </header>

      <section className="card">
        <h2>Connexion</h2>
        <form onSubmit={submit}>
          <label className="field">
            <span className="field__label">Mot de passe</span>
            <input
              type="password"
              value={password}
              autoFocus
              autoComplete="current-password"
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>

          <label className="toggle">
            <input
              type="checkbox"
              checked={remember}
              onChange={(event) => setRemember(event.target.checked)}
            />
            Rester connecté sur ce navigateur pendant trente jours
          </label>

          {error && <p className="state state--error">{error}</p>}

          <div className="actions">
            <button type="submit" className="btn" disabled={busy || !password}>
              {busy ? "Vérification…" : "Se connecter"}
            </button>
          </div>
        </form>

        <p className="field__help">
          Mot de passe perdu ? Supprimez le fichier <code>session.key</code> et
          l'entrée <code>auth.password_hash</code> de la base, dans l'appdata du
          conteneur.
        </p>
      </section>
    </main>
  );
}
