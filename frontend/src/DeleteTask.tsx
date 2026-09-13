import { useState } from "react";

import type { Task } from "./api";

type Props = {
  task: Task;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
};

/**
 * Confirmation renforcée avant suppression d'une tâche destructive
 * (TASK-005, §10.4).
 *
 * La peur naturelle — « vais-je perdre mes fichiers ? » — est infondée, et
 * le dire est la première chose à faire. Les vraies pertes sont ailleurs, et
 * invisibles : l'historique, la corbeille qui ne sera plus purgée, et pour le
 * bidirectionnel la référence commune, dont la disparition impose une fusion
 * à la prochaine tâche équivalente.
 */
export default function DeleteTask({ task, busy, onConfirm, onCancel }: Props) {
  const [acknowledged, setAcknowledged] = useState(false);
  const bidirectionnel = task.mode === "bisync";

  return (
    <div className="blocked">
      <strong>Supprimer la tâche «&nbsp;{task.name}&nbsp;» ?</strong>

      <p>
        <strong>Vos fichiers ne sont pas touchés</strong>, ni en local ni à
        distance. Seule la configuration de la tâche disparaît.
      </p>

      <p>Ce qui est perdu en revanche :</p>
      <ul className="blocked__paths">
        <li>l'historique de ses exécutions et le détail fichier par fichier</li>
        {task.quarantine_enabled && (
          <li>
            la purge automatique de la corbeille <code>.cloudsync-trash</code> à
            destination — le dossier restera, et c'est à vous de le vider
          </li>
        )}
        {bidirectionnel && (
          <li>
            la référence commune aux deux côtés : recréer une tâche
            équivalente imposera une nouvelle initialisation, donc une fusion
          </li>
        )}
      </ul>

      <label className="toggle">
        <input
          type="checkbox"
          checked={acknowledged}
          onChange={(event) => setAcknowledged(event.target.checked)}
        />
        J'ai compris ce qui sera perdu.
      </label>

      <div className="actions">
        <button
          type="button"
          className="btn btn--danger"
          disabled={!acknowledged || busy}
          onClick={onConfirm}
        >
          Supprimer la tâche et son historique
        </button>
        <button
          type="button"
          className="btn btn--ghost"
          disabled={busy}
          onClick={onCancel}
        >
          Annuler
        </button>
      </div>
    </div>
  );
}
