import { useState } from "react";

import { setPassword, type AuthState } from "./api";

type Props = {
  auth: AuthState;
  onChanged: () => void;
};

/** Réglage du mot de passe de l'interface (SEC-006). */
export default function PasswordPanel({ auth, onChanged }: Props) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function apply(newPassword: string, success: string) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await setPassword({
        ...(auth.enabled ? { current_password: current } : {}),
        new_password: newPassword,
      });
      setCurrent("");
      setNext("");
      setMessage(success);
      onChanged();
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Accès à l'interface</h2>

      {!auth.enabled && (
        <div className="blocked">
          <strong>L'interface n'est protégée par aucun mot de passe.</strong>
          <p>
            Toute personne pouvant joindre ce port sur votre réseau peut créer une
            tâche et déclencher des suppressions. Sur un réseau domestique le risque
            reste limité, mais il n'est pas nul — et si l'application est un jour
            atteignable depuis Internet, il devient sérieux.
          </p>
          <p>
            N'exposez jamais ce port directement depuis votre box. Pour un accès
            distant, passez par un reverse proxy HTTPS.
          </p>
        </div>
      )}

      {auth.enabled && (
        <label className="field">
          <span className="field__label">Mot de passe actuel</span>
          <input
            type="password"
            value={current}
            autoComplete="current-password"
            onChange={(event) => setCurrent(event.target.value)}
          />
        </label>
      )}

      <label className="field">
        <span className="field__label">
          {auth.enabled ? "Nouveau mot de passe" : "Mot de passe"}
        </span>
        <input
          type="password"
          value={next}
          autoComplete="new-password"
          onChange={(event) => setNext(event.target.value)}
        />
        <span className="field__help">Huit caractères au minimum.</span>
      </label>

      {error && <p className="state state--error">{error}</p>}
      {message && <p className="state">{message}</p>}

      <div className="actions">
        <button
          type="button"
          className="btn"
          disabled={busy || next.length < 8 || (auth.enabled && !current)}
          onClick={() =>
            void apply(next, auth.enabled ? "Mot de passe modifié." : "Interface protégée.")
          }
        >
          {auth.enabled ? "Changer le mot de passe" : "Protéger l'interface"}
        </button>

        {auth.enabled && (
          <button
            type="button"
            className="btn btn--ghost"
            disabled={busy || !current}
            onClick={() => {
              if (window.confirm("Retirer le mot de passe et laisser l'interface ouverte ?")) {
                void apply("", "Protection retirée.");
              }
            }}
          >
            Retirer la protection
          </button>
        )}
      </div>
    </section>
  );
}
