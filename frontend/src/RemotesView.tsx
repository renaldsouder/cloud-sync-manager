import { useCallback, useEffect, useState } from "react";

import OAuthPanel from "./OAuthPanel";
import RemoteWizard from "./RemoteWizard";
import {
  completeRemote,
  deleteRemote,
  fetchRemotes,
  formatBytes,
  reauthorizeRemote,
  testRemote,
  type Remote,
  type RemoteTest,
  type Versioning,
} from "./api";

/** Fournisseurs dont l'accès passe par un jeton OAuth (CLOUD-003). */
const OAUTH = new Set(["drive", "onedrive", "dropbox"]);

const STATUS_LABEL: Record<Remote["status"], string> = {
  ok: "Accessible",
  error: "Erreur",
  unknown: "Jamais testé",
};

export default function RemotesView() {
  const [remotes, setRemotes] = useState<Remote[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, RemoteTest>>({});
  const [reauth, setReauth] = useState<string | null>(null);
  const [notice, setNotice] = useState<Record<string, string>>({});

  const reload = useCallback(async () => {
    try {
      setRemotes(await fetchRemotes());
      setError(null);
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function runTest(remote: Remote) {
    setBusyId(remote.id);
    try {
      const result = await testRemote(remote.id);
      setResults((previous) => ({ ...previous, [remote.id]: result }));
      await reload();
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyId(null);
    }
  }

  async function act(remote: Remote, action: () => Promise<unknown>) {
    setBusyId(remote.id);
    setError(null);
    try {
      await action();
      await reload();
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyId(null);
    }
  }

  async function reauthorize(remote: Remote, options: Record<string, string>) {
    setBusyId(remote.id);
    setError(null);
    try {
      const done = await reauthorizeRemote(remote.id, options);
      setReauth(null);
      const rearmees = done.simulation_required;
      setNotice((previous) => ({
        ...previous,
        [remote.id]:
          "Accès ré-autorisé. Testez le stockage pour le confirmer." +
          (rearmees.length > 0
            ? ` Par prudence, ces tâches devront d'abord être simulées : ${rearmees.join(", ")}.`
            : ""),
      }));
      await reload();
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyId(null);
    }
  }

  async function remove(remote: Remote) {
    if (!window.confirm(`Supprimer le stockage « ${remote.name} » ?`)) return;
    setBusyId(remote.id);
    try {
      await deleteRemote(remote.id);
      await reload();
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyId(null);
    }
  }

  if (adding) {
    return (
      <RemoteWizard
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
        <h2>Stockages Cloud</h2>
        <button type="button" className="btn" onClick={() => setAdding(true)}>
          Ajouter un stockage
        </button>
      </div>

      {error && <p className="state state--error">{error}</p>}
      {!remotes && !error && <p className="state">Chargement…</p>}

      {remotes?.length === 0 && (
        <p className="state">
          Aucun stockage pour l'instant. Commencez par en ajouter un, puis testez-le
          avant de créer une tâche.
        </p>
      )}

      <ul className="remotes">
        {remotes?.map((remote) => {
          const result = results[remote.id];
          return (
            <li key={remote.id} className="remote">
              <div className="remote__head">
                <span className={`dot dot--${remote.status}`} aria-hidden="true" />
                <span className="remote__name">{remote.name}</span>
                <span className="remote__provider">{remote.provider}</span>
                <span className="remote__status">{STATUS_LABEL[remote.status]}</span>
              </div>

              <dl className="remote__options">
                {Object.entries(remote.options).map(([key, value]) => (
                  <div key={key}>
                    <dt>{key}</dt>
                    <dd className={value === "configuré" ? "muted" : undefined}>{value}</dd>
                  </div>
                ))}
              </dl>

              {remote.versioning && <Versions versioning={remote.versioning} />}

              {result && (
                <p className={`state ${result.ok ? "" : "state--error"}`}>
                  {result.detail} · {result.elapsed_ms} ms
                  {result.ok && result.entries.length > 0 && (
                    <> · {result.entries.slice(0, 6).join(", ")}</>
                  )}
                </p>
              )}
              {result?.ok && <Capacites capabilities={result.capabilities} />}

              {remote.provider === "onedrive" && !remote.options.drive_id && (
                <p className="state state--error">
                  Ce stockage OneDrive n'a pas d'identifiant de disque : Microsoft
                  ne l'a pas livré au moment de l'autorisation. Le jeton, lui, est
                  enregistré — un nouvel essai suffit généralement.
                </p>
              )}

              {notice[remote.id] && <p className="state">{notice[remote.id]}</p>}

              {reauth === remote.id && (
                <>
                  <p className="field__help">
                    Pour un jeton expiré ou révoqué. Connectez-vous avec{" "}
                    <strong>le même compte</strong> : seul le jeton est remplacé, les
                    tâches qui utilisent ce stockage restent en place.
                  </p>
                  <OAuthPanel
                    existing
                    provider={remote.provider}
                    label={remote.name}
                    onAuthorized={(options) => void reauthorize(remote, options)}
                  />
                </>
              )}

              <div className="actions">
                {OAUTH.has(remote.provider) && (
                  <button
                    type="button"
                    className="btn btn--small btn--ghost"
                    disabled={busyId === remote.id}
                    onClick={() => setReauth(reauth === remote.id ? null : remote.id)}
                  >
                    {reauth === remote.id ? "Annuler la ré-autorisation" : "Ré-autoriser"}
                  </button>
                )}
                {remote.provider === "onedrive" && !remote.options.drive_id && (
                  <button
                    type="button"
                    className="btn btn--small"
                    disabled={busyId === remote.id}
                    onClick={() => void act(remote, () => completeRemote(remote.id))}
                  >
                    Compléter la configuration
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn--small"
                  disabled={busyId === remote.id}
                  onClick={() => void runTest(remote)}
                >
                  {busyId === remote.id ? "Test…" : "Tester"}
                </button>
                <button
                  type="button"
                  className="btn btn--small btn--ghost"
                  disabled={busyId === remote.id}
                  onClick={() => void remove(remote)}
                >
                  Supprimer
                </button>
                {remote.task_count > 0 && (
                  <span className="field__help">
                    {remote.task_count} tâche(s) associée(s)
                  </span>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/**
 * Ce que le test a constaté au-delà de la simple connexion (§9.3) : l'espace
 * disponible, quand le fournisseur sait le dire, et les sommes de contrôle
 * qui rendent possible la comparaison par contenu (SYNC-005).
 */
const DEVENIR: Record<Versioning["deleted"], string> = {
  kept: "récupérable chez le fournisseur",
  permanent: "non conservé par le fournisseur",
  unknown: "inconnu",
};

/**
 * Filet du fournisseur (CONF-005). Présenté comme un complément : la
 * corbeille de l'application et les seuils s'appliquent dans tous les cas,
 * et l'interface ne doit jamais laisser croire le contraire.
 */
function Versions({ versioning }: { versioning: Versioning }) {
  return (
    <p className="state">
      <strong>Chez le fournisseur</strong> — fichier supprimé :{" "}
      {DEVENIR[versioning.deleted]} ; fichier remplacé :{" "}
      {DEVENIR[versioning.replaced]}. {versioning.detail} Un complément
      seulement : la corbeille de l'application et les seuils de suppression
      restent appliqués.
    </p>
  );
}

function Capacites({ capabilities }: { capabilities: Record<string, unknown> }) {
  const quota = capabilities.quota as
    | { used?: number; free?: number; total?: number }
    | undefined;
  const hashes = capabilities.hashes as string[] | undefined;
  const morceaux: string[] = [];
  if (quota && typeof quota.used === "number") {
    morceaux.push(
      typeof quota.free === "number"
        ? `${formatBytes(quota.used)} utilisés, ${formatBytes(quota.free)} libres`
        : `${formatBytes(quota.used)} utilisés`,
    );
  }
  if (hashes) {
    morceaux.push(
      hashes.length
        ? `sommes de contrôle : ${hashes.slice(0, 3).join(", ")}${hashes.length > 3 ? "…" : ""}`
        : "aucune somme de contrôle",
    );
  }
  if (!morceaux.length) return null;
  return <p className="state">{morceaux.join(" · ")}</p>;
}
