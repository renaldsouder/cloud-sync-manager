import { useEffect, useState } from "react";

import { fetchEvents, fetchRun, type Run, type TaskEvent } from "./api";

type Props = {
  runId: string;
  taskName: string;
  onConfirm: () => void;
};

/**
 * Confirmation renforcée d'une exécution bloquée par le seuil (§8.3, §10.4).
 *
 * Deux règles du §10.4 sont tenues ici : le bouton nomme l'action réelle —
 * « Supprimer 423 fichiers », jamais « Confirmer » — et l'utilisateur voit
 * *quels* fichiers disparaîtraient, pas seulement combien.
 */
export default function BlockedRun({ runId, taskName, onConfirm }: Props) {
  const [run, setRun] = useState<Run | null>(null);
  const [paths, setPaths] = useState<TaskEvent[]>([]);
  const [showAll, setShowAll] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setAcknowledged(false);
    setShowAll(false);
    void Promise.all([
      fetchRun(runId, controller.signal),
      fetchEvents(runId, { kind: "skip_delete" }, controller.signal),
    ])
      .then(([loadedRun, loadedPaths]) => {
        setRun(loadedRun);
        setPaths(loadedPaths);
      })
      .catch(() => {
        /* l'écran parent affiche déjà les erreurs de chargement */
      });
    return () => controller.abort();
  }, [runId]);

  if (!run) return null;

  const reason = run.summary?.blocked_reason;
  const plan = run.summary?.deletion_plan;
  const count = plan?.deletes ?? paths.length;
  const visible = showAll ? paths : paths.slice(0, 10);

  return (
    <div className="blocked">
      <strong>Tâche bloquée — validation nécessaire</strong>
      {reason && <p>{reason}</p>}
      {plan && (
        <p>
          Cela représente <strong>{plan.percent} %</strong> des {plan.checks} éléments
          présents à destination. Rien n'a été supprimé.
        </p>
      )}

      {paths.length > 0 && (
        <>
          <ul className="blocked__paths">
            {visible.map((event) => (
              <li key={event.id}>{event.path}</li>
            ))}
          </ul>
          {paths.length > visible.length && (
            <button
              type="button"
              className="btn btn--small btn--ghost"
              onClick={() => setShowAll(true)}
            >
              Afficher les {paths.length} fichiers
            </button>
          )}
        </>
      )}

      <label className="toggle">
        <input
          type="checkbox"
          checked={acknowledged}
          onChange={(event) => setAcknowledged(event.target.checked)}
        />
        J'ai vérifié la source et je veux appliquer ces suppressions à «&nbsp;{taskName}&nbsp;».
      </label>

      <div className="actions">
        <button
          type="button"
          className="btn btn--danger"
          disabled={!acknowledged}
          onClick={onConfirm}
        >
          Supprimer {count} fichier{count > 1 ? "s" : ""}
        </button>
      </div>

      <p className="field__help">
        Les fichiers supprimés sont déplacés dans <code>.cloudsync-trash</code> à
        destination : ils restent récupérables.
      </p>
    </div>
  );
}
