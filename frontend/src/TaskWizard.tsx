import { useEffect, useState } from "react";

import { createTask, fetchRemotes, type Remote } from "./api";

type Props = {
  onCreated: () => void;
  onCancel: () => void;
};

type Direction = "local_to_remote" | "remote_to_local";
type Mode = "copy" | "mirror";

/**
 * Assistant de création d'une tâche (UI-002).
 *
 * Le §10.4 est le fil conducteur : une option destructive n'est jamais un
 * simple interrupteur, et les conséquences sont écrites en toutes lettres
 * avant validation.
 */
export default function TaskWizard({ onCreated, onCancel }: Props) {
  const [remotes, setRemotes] = useState<Remote[]>([]);
  const [name, setName] = useState("");
  const [remoteId, setRemoteId] = useState("");
  const [localPath, setLocalPath] = useState("/mnt/user/");
  const [remotePath, setRemotePath] = useState("");
  const [direction, setDirection] = useState<Direction>("local_to_remote");
  const [mode, setMode] = useState<Mode>("copy");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
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
  }, []);

  const canSubmit =
    name.trim().length > 0 && remoteId.length > 0 && localPath.trim().length > 1;

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await createTask({
        name: name.trim(),
        remote_id: remoteId,
        local_path: localPath.trim(),
        remote_path: remotePath.trim(),
        direction,
        mode,
      });
      onCreated();
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  if (remotes.length === 0) {
    return (
      <section className="card">
        <div className="card__head">
          <h2>Nouvelle tâche</h2>
          <button type="button" className="btn btn--ghost" onClick={onCancel}>
            Retour
          </button>
        </div>
        <p className="state">
          Ajoutez d'abord un stockage Cloud, puis testez-le : une tâche a besoin
          d'une destination joignable.
        </p>
      </section>
    );
  }

  return (
    <section className="card">
      <div className="card__head">
        <h2>Nouvelle tâche</h2>
        <button type="button" className="btn btn--ghost" onClick={onCancel}>
          Annuler
        </button>
      </div>

      <label className="field">
        <span className="field__label">Nom de la tâche</span>
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Photos vers Google Drive"
        />
      </label>

      <label className="field">
        <span className="field__label">Stockage Cloud</span>
        <select value={remoteId} onChange={(event) => setRemoteId(event.target.value)}>
          {remotes.map((remote) => (
            <option key={remote.id} value={remote.id}>
              {remote.name}
              {remote.status !== "ok" && " — jamais testé avec succès"}
            </option>
          ))}
        </select>
      </label>

      <fieldset className="choices">
        <legend className="field__label">Sens</legend>
        {(
          [
            ["local_to_remote", "Local → Cloud", "Envoie vos fichiers vers le Cloud."],
            [
              "remote_to_local",
              "Cloud → Local",
              "Écrit dans le share Unraid choisi ci-dessous.",
            ],
          ] as [Direction, string, string][]
        ).map(([value, label, help]) => (
          <label className="choice" key={value}>
            <input
              type="radio"
              name="direction"
              checked={direction === value}
              onChange={() => setDirection(value)}
            />
            <span>
              <strong>{label}</strong>
              <span className="field__help">{help}</span>
            </span>
          </label>
        ))}
      </fieldset>

      <fieldset className="choices">
        <legend className="field__label">Mode</legend>
        <label className="choice">
          <input
            type="radio"
            name="mode"
            checked={mode === "copy"}
            onChange={() => setMode("copy")}
          />
          <span>
            <strong>Copie</strong>
            <span className="field__help">
              Ajoute et met à jour les fichiers. <strong>Ne supprime jamais rien</strong> à
              destination.
            </span>
          </span>
        </label>
        <label className="choice">
          <input
            type="radio"
            name="mode"
            checked={mode === "mirror"}
            onChange={() => setMode("mirror")}
          />
          <span>
            <strong>Miroir</strong>
            <span className="field__help">
              La destination devient le reflet exact de la source :{" "}
              <strong>tout ce qui n'existe plus à la source y sera supprimé</strong>. Une
              simulation sera obligatoire avant la première exécution.
            </span>
          </span>
        </label>
      </fieldset>

      {mode === "mirror" && (
        <div className="notice">
          <strong>Protections appliquées à ce mode</strong>
          <p>
            Une simulation est obligatoire avant la première exécution. Ensuite,
            chaque lancement mesure d'abord ce qui serait supprimé : si la source
            paraît vide ou inaccessible, ou si le nombre de suppressions dépasse le
            seuil de la tâche, rien n'est touché et votre validation est demandée.
            Les fichiers supprimés sont déplacés dans une corbeille à destination.
          </p>
        </div>
      )}

      <label className="field">
        <span className="field__label">Dossier local</span>
        <input
          value={localPath}
          onChange={(event) => setLocalPath(event.target.value)}
          placeholder="/mnt/user/Photos"
        />
        <span className="field__help">
          Doit se trouver dans un chemin monté dans le conteneur. Les shares
          <code> appdata</code>, <code>system</code> et <code>domains</code> ne peuvent
          pas être une destination.
        </span>
      </label>

      <label className="field">
        <span className="field__label">Dossier distant</span>
        <input
          value={remotePath}
          onChange={(event) => setRemotePath(event.target.value)}
          placeholder="Sauvegardes/Photos"
        />
        <span className="field__help">Laisser vide pour la racine du stockage.</span>
      </label>

      {error && <p className="state state--error">{error}</p>}

      <div className="actions">
        <button type="button" className="btn" disabled={!canSubmit || busy} onClick={submit}>
          {busy ? "Création…" : "Créer la tâche"}
        </button>
        <button type="button" className="btn btn--ghost" onClick={onCancel}>
          Annuler
        </button>
      </div>
    </section>
  );
}
