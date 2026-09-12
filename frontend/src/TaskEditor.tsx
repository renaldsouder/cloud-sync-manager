import { useEffect, useState } from "react";

import {
  fetchFilterSets,
  updateTask,
  type FilterSet,
  type Task,
} from "./api";

type Props = {
  task: Task;
  onSaved: () => void;
  onCancel: () => void;
};

const DIRECTION_LABEL: Record<Task["direction"], string> = {
  local_to_remote: "Local → Cloud",
  remote_to_local: "Cloud → Local",
};

const MODE_LABEL: Record<Task["mode"], string> = {
  copy: "Copie",
  mirror: "Miroir",
  bisync: "Bidirectionnel",
};

/**
 * Modification d'une tâche existante (TASK-001).
 *
 * Le sens et le mode sont affichés mais non modifiables : les changer
 * contournerait la simulation que le §8.1 exige après tout changement de
 * source, de destination ou de sens. Les montrer grisés vaut mieux que de
 * les cacher — l'utilisateur voit ce qu'il a choisi et pourquoi il ne peut
 * plus y revenir.
 */
export default function TaskEditor({ task, onSaved, onCancel }: Props) {
  const [name, setName] = useState(task.name);
  const [localPath, setLocalPath] = useState(task.local_path);
  const [remotePath, setRemotePath] = useState(task.remote_path);
  const [filterSetId, setFilterSetId] = useState(task.filter_set_id ?? "");
  const [maxDeletes, setMaxDeletes] = useState(String(task.max_deletes ?? 0));
  const [maxPercent, setMaxPercent] = useState(
    String(task.max_delete_percent ?? 0),
  );
  const [quarantine, setQuarantine] = useState(task.quarantine_enabled);

  const [filterSets, setFilterSets] = useState<FilterSet[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetchFilterSets(controller.signal)
      .then(setFilterSets)
      .catch(() => {
        /* l'absence de jeux de filtres n'empêche pas de modifier le reste */
      });
    return () => controller.abort();
  }, []);

  const cheminChange =
    localPath !== task.local_path || remotePath !== task.remote_path;
  const corbeilleRetiree = task.quarantine_enabled && !quarantine;
  const destructif = task.mode !== "copy";

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await updateTask(task.id, {
        name: name.trim(),
        local_path: localPath.trim(),
        remote_path: remotePath.trim(),
        filter_set_id: filterSetId || null,
        max_deletes: Number(maxDeletes) || 0,
        max_delete_percent: Number(maxPercent) || 0,
        quarantine_enabled: quarantine,
      });
      onSaved();
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="card">
      <div className="card__head">
        <h2>Modifier «&nbsp;{task.name}&nbsp;»</h2>
      </div>

      <label className="field">
        <span className="field__label">Nom</span>
        <input value={name} onChange={(event) => setName(event.target.value)} />
      </label>

      <label className="field">
        <span className="field__label">Dossier local</span>
        <input
          value={localPath}
          onChange={(event) => setLocalPath(event.target.value)}
        />
      </label>

      <label className="field">
        <span className="field__label">Dossier distant</span>
        <input
          value={remotePath}
          placeholder="laisser vide pour la racine"
          onChange={(event) => setRemotePath(event.target.value)}
        />
      </label>

      <div className="field">
        <span className="field__label">Sens et mode</span>
        <p className="path">
          <span className="chip">{DIRECTION_LABEL[task.direction]}</span>
          <span className="chip">{MODE_LABEL[task.mode]}</span>
        </p>
        <span className="field__help">
          Ni l'un ni l'autre ne se modifie sur une tâche existante : ce serait
          repartir d'une comparaison différente sans l'avoir simulée. Pour en
          changer, créez une nouvelle tâche.
        </span>
      </div>

      <label className="field">
        <span className="field__label">Jeu de filtres</span>
        <select
          value={filterSetId}
          onChange={(event) => setFilterSetId(event.target.value)}
        >
          <option value="">Aucun</option>
          {filterSets.map((jeu) => (
            <option key={jeu.id} value={jeu.id}>
              {jeu.name}
            </option>
          ))}
        </select>
      </label>

      {destructif && (
        <>
          <label className="field">
            <span className="field__label">
              Bloquer au-delà de … fichiers supprimés
            </span>
            <input
              type="number"
              min={0}
              value={maxDeletes}
              onChange={(event) => setMaxDeletes(event.target.value)}
            />
            <span className="field__help">0 désactive ce critère.</span>
          </label>

          <label className="field">
            <span className="field__label">
              Bloquer au-delà de … % de la destination
            </span>
            <input
              type="number"
              min={0}
              max={100}
              value={maxPercent}
              onChange={(event) => setMaxPercent(event.target.value)}
            />
            <span className="field__help">
              Au-delà de l'un ou l'autre, la tâche s'arrête et attend votre
              validation, en nommant les fichiers concernés.
            </span>
          </label>

          <label className="toggle">
            <input
              type="checkbox"
              checked={quarantine}
              onChange={(event) => setQuarantine(event.target.checked)}
            />
            Déplacer les suppressions dans une corbeille récupérable
          </label>
        </>
      )}

      {corbeilleRetiree && (
        <p className="state state--error">
          Sans corbeille, les suppressions deviennent définitives. Une
          simulation sera exigée avant la prochaine exécution.
        </p>
      )}

      {cheminChange && destructif && (
        <p className="state state--error">
          Changer un dossier réarme la simulation obligatoire : la prochaine
          exécution devra être simulée avant d'être appliquée.
        </p>
      )}

      {error && <p className="state state--error">{error}</p>}

      <div className="actions">
        <button type="button" className="btn" disabled={saving} onClick={() => void save()}>
          {saving ? "Enregistrement…" : "Enregistrer"}
        </button>
        <button
          type="button"
          className="btn btn--ghost"
          disabled={saving}
          onClick={onCancel}
        >
          Annuler
        </button>
      </div>
    </section>
  );
}
