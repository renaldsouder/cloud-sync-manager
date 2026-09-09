import { useState } from "react";

import { completeOAuth, startOAuth } from "./api";

type Props = {
  provider: string;
  label: string;
  onAuthorized: (options: Record<string, string>) => void;
};

/**
 * Autorisation OAuth conduite depuis l'interface (FIRST-002, P9).
 *
 * Le §1.4 interdit de faire passer le parcours normal par un terminal. Il
 * reste un copier-coller, faute de pouvoir contourner l'URL de redirection
 * que rclone a enregistrée chez les fournisseurs — mais plus d'installation
 * ni de dialogue en ligne de commande.
 */
export default function OAuthPanel({ provider, label, onAuthorized }: Props) {
  const [session, setSession] = useState<string | null>(null);
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [redirect, setRedirect] = useState("");
  const [warning, setWarning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function begin() {
    setBusy(true);
    setError(null);
    setWarning(null);
    try {
      const started = await startOAuth(provider);
      setSession(started.session_id);
      setAuthUrl(started.auth_url);
      window.open(started.auth_url, "_blank", "noopener");
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function finish() {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      const done = await completeOAuth(session, redirect.trim());
      setWarning(done.warning ?? null);
      setSession(null);
      setAuthUrl(null);
      setRedirect("");
      onAuthorized(done.options);
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="notice">
      <strong>Autoriser l'accès à {label}</strong>

      {!session && (
        <>
          <p>
            Une fenêtre s'ouvrira sur la page de connexion du fournisseur. Vous
            reviendrez ensuite ici pour terminer.
          </p>
          <div className="actions">
            <button type="button" className="btn btn--small" disabled={busy} onClick={begin}>
              {busy ? "Préparation…" : "Autoriser l'accès"}
            </button>
          </div>
        </>
      )}

      {session && (
        <>
          <p>
            <strong>1.</strong> Connectez-vous et autorisez l'accès dans l'onglet qui
            vient de s'ouvrir. Si rien ne s'est ouvert,{" "}
            <a href={authUrl ?? "#"} target="_blank" rel="noreferrer">
              suivez ce lien
            </a>
            .
          </p>
          <p>
            <strong>2.</strong> Votre navigateur atterrira sur une page d'erreur de
            connexion — <em>c'est normal et attendu</em>. Copiez l'adresse complète
            de cette page depuis la barre d'adresse, et collez-la ci-dessous.
          </p>

          <label className="field">
            <span className="field__label">Adresse de la page d'erreur</span>
            <input
              value={redirect}
              placeholder="http://localhost:53682/?code=…&amp;state=…"
              onChange={(event) => setRedirect(event.target.value)}
            />
          </label>

          <div className="actions">
            <button
              type="button"
              className="btn btn--small"
              disabled={busy || !redirect.includes("code=")}
              onClick={finish}
            >
              {busy ? "Vérification…" : "Terminer l'autorisation"}
            </button>
          </div>
        </>
      )}

      {warning && <p className="state state--error">{warning}</p>}
      {error && <p className="state state--error">{error}</p>}
    </div>
  );
}
