import { useCallback, useEffect, useState } from "react";

import LocalPicker from "./LocalPicker";
import RemotePicker from "./RemotePicker";
import {
  fetchOneShotCopies,
  fetchRemotes,
  formatBytes,
  formatDate,
  startOneShotCopy,
  subscribeToRuns,
  type OneShotCopy as Copie,
  type Remote,
} from "./api";

const STATUT: Record<string, string> = {
  running: "En cours",
  success: "Réussie",
  warning: "Avertissement",
  error: "Erreur",
  blocked: "Bloquée",
  interrupted: "Interrompue",
};

/**
 * Copie ponctuelle (SYNC-008).
 *
 * Pour envoyer un dossier une fois, sans créer une tâche qu'il faudrait
 * ensuite penser à supprimer. C'est toujours une Copie : une opération
 * lancée en passant ne doit jamais pouvoir effacer quoi que ce soit, d'où
 * l'absence de tout choix de mode ici.
 */
export default function OneShotCopy() {
  const [ouvert, setOuvert] = useState(false);
  const [remotes, setRemotes] = useState<Remote[]>([]);
  const [remoteId, setRemoteId] = useState("");
  const [localPath, setLocalPath] = useState("/mnt/user/");
  const [remotePath, setRemotePath] = useState("");
  const [direction, setDirection] = useState("local_to_remote");
  const [simulation, setSimulation] = useState(false);
  const [parcourt, setParcourt] = useState(false);
  const [parcourtDistant, setParcourtDistant] = useState(false);
  const [copies, setCopies] = useState<Copie[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const recharger = useCallback(async () => {
    try {
      setCopies(await fetchOneShotCopies());
    } catch {
      // La liste est un complément : son échec ne doit pas masquer le reste.
    }
  }, []);

  useEffect(() => {
    void recharger();
  }, [recharger]);

  // Une copie qui se termine doit changer d'état sans recharger la page.
  // Une copie courte peut finir avant la première trame du flux : tant
  // qu'une copie affichée est « en cours », on relit la liste à chaque trame.
  const attend = copies.some(({ run }) => run?.status === "running");
  useEffect(() => {
    let enCours = 0;
    return subscribeToRuns((runs) => {
      if (attend || runs.length !== enCours) {
        enCours = runs.length;
        void recharger();
      }
    });
  }, [attend, recharger]);

  useEffect(() => {
    if (!ouvert || remotes.length > 0) return;
    const controller = new AbortController();
    fetchRemotes(controller.signal)
      .then((found) => {
        setRemotes(found);
        if (found.length > 0) setRemoteId(found[0].id);
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      });
    return () => controller.abort();
  }, [ouvert, remotes.length]);

  async function lancer() {
    setBusy(true);
    setError(null);
    try {
      await startOneShotCopy({
        remote_id: remoteId,
        local_path: localPath.trim(),
        remote_path: remotePath.trim(),
        direction,
        dry_run: simulation,
      });
      setOuvert(false);
      await recharger();
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <div className="card__head">
        <h2>Copie ponctuelle</h2>
        {!ouvert && (
          <button type="button" className="btn btn--ghost" onClick={() => setOuvert(true)}>
            Copier une fois…
          </button>
        )}
      </div>

      {!ouvert && copies.length === 0 && (
        <p className="state">
          Pour copier un dossier une seule fois, sans créer de tâche. Une copie
          ponctuelle ajoute et met à jour des fichiers, elle n'en supprime jamais.
        </p>
      )}

      {ouvert && (
        <div>
          <p className="field__help">
            Toujours une <strong>Copie</strong> : rien n'est supprimé à destination,
            même si des fichiers y manquent à la source.
          </p>

          <label className="field">
            <span className="field__label">Stockage</span>
            <select value={remoteId} onChange={(event) => setRemoteId(event.target.value)}>
              {remotes.map((remote) => (
                <option key={remote.id} value={remote.id}>
                  {remote.name}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span className="field__label">Sens</span>
            <select value={direction} onChange={(event) => setDirection(event.target.value)}>
              <option value="local_to_remote">Local → Cloud</option>
              <option value="remote_to_local">Cloud → Local</option>
            </select>
          </label>

          <label className="field">
            <span className="field__label">Dossier local</span>
            <input value={localPath} onChange={(event) => setLocalPath(event.target.value)} />
            <span className="actions">
              <button
                type="button"
                className="btn btn--small btn--ghost"
                onClick={() => setParcourt((valeur) => !valeur)}
              >
                {parcourt ? "Fermer l'explorateur" : "Parcourir les dossiers…"}
              </button>
            </span>
          </label>
          {parcourt && (
            <LocalPicker
              valeur={localPath}
              destination={direction === "remote_to_local"}
              onFermer={() => setParcourt(false)}
              onChoisir={(chemin) => {
                setLocalPath(chemin);
                setParcourt(false);
              }}
            />
          )}

          <label className="field">
            <span className="field__label">Dossier distant</span>
            <input
              value={remotePath}
              placeholder="racine du stockage"
              onChange={(event) => setRemotePath(event.target.value)}
            />
            {remoteId && (
              <span className="actions">
                <button
                  type="button"
                  className="btn btn--small btn--ghost"
                  onClick={() => setParcourtDistant((valeur) => !valeur)}
                >
                  {parcourtDistant
                    ? "Fermer l'explorateur"
                    : "Parcourir le stockage distant…"}
                </button>
              </span>
            )}
          </label>
          {parcourtDistant && remoteId && (
            <RemotePicker
              remoteId={remoteId}
              valeur={remotePath}
              onFermer={() => setParcourtDistant(false)}
              onChoisir={(chemin) => {
                setRemotePath(chemin);
                setParcourtDistant(false);
              }}
            />
          )}

          <label className="toggle">
            <input
              type="checkbox"
              checked={simulation}
              onChange={(event) => setSimulation(event.target.checked)}
            />
            <span>Simuler seulement, sans rien copier</span>
          </label>

          {remotes.length === 0 && (
            <p className="state">Ajoutez d'abord un stockage dans l'onglet Stockages.</p>
          )}
          {error && <p className="state state--error">{error}</p>}

          <div className="actions">
            <button
              type="button"
              className="btn"
              disabled={busy || !remoteId || localPath.trim().length < 2}
              onClick={() => void lancer()}
            >
              {busy ? "Lancement…" : simulation ? "Simuler" : "Copier"}
            </button>
            <button type="button" className="btn btn--ghost" onClick={() => setOuvert(false)}>
              Annuler
            </button>
          </div>
        </div>
      )}

      {copies.length > 0 && (
        <ul className="remotes">
          {copies.map(({ task, run }) => (
            <li key={task.id} className="remote">
              <div className="remote__head">
                <span className={`dot dot--${run?.status ?? "ready"}`} aria-hidden="true" />
                <span className="remote__name">
                  {task.local_path} {task.direction === "local_to_remote" ? "→" : "←"} Cloud
                  : {task.remote_path && task.remote_path !== "." ? task.remote_path : "racine"}
                </span>
                <span className="remote__status">
                  {run ? (STATUT[run.status] ?? run.status) : "Non lancée"}
                  {run?.dry_run ? " (simulation)" : ""}
                </span>
              </div>
              {run && (
                <p className="field__help">
                  {formatDate(run.started_at)} · {run.transferred_files} fichier(s),{" "}
                  {formatBytes(run.transferred_bytes)}
                  {run.errors_count > 0 ? ` · ${run.errors_count} erreur(s)` : ""}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
