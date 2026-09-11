import { useCallback, useEffect, useState } from "react";

import {
  fetchBisync,
  placeAccessMarkers,
  updateBisync,
  type BisyncSettings,
} from "./api";

type Props = {
  taskId: string;
  taskName: string;
  busy: boolean;
  onSimulateResync: () => void;
  onApplyResync: () => void;
};

const RESOLVE_LABEL: Record<string, string> = {
  none: "Conserver les deux versions",
  newer: "Garder la plus récente",
  older: "Garder la plus ancienne",
  larger: "Garder la plus grosse",
  smaller: "Garder la plus petite",
  path1: "Garder la version du premier côté",
  path2: "Garder la version du second côté",
};

/**
 * Conduite d'une tâche bidirectionnelle (SYNC-003, §7.3, §8.1).
 *
 * Deux choses doivent être impossibles à manquer ici. La ré-initialisation
 * fusionne les deux côtés, donc elle se simule avant de s'appliquer. Et le
 * traitement par défaut d'un conflit conserve les deux versions mais fait
 * disparaître le nom d'origine : sans cet avertissement, l'utilisateur
 * croira le fichier perdu.
 */
export default function BisyncPanel({
  taskId,
  taskName,
  busy,
  onSimulateResync,
  onApplyResync,
}: Props) {
  const [settings, setSettings] = useState<BisyncSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  const reload = useCallback(async () => {
    try {
      setSettings(await fetchBisync(taskId));
      setError(null);
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [taskId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function act(action: () => Promise<unknown>, message?: string) {
    setWorking(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      if (message) setNotice(message);
      await reload();
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setWorking(false);
    }
  }

  if (!settings) return null;

  const occupe = busy || working;

  return (
    <div className="bisync">
      <strong>Synchronisation bidirectionnelle</strong>

      {!settings.initialised && (
        <p className="state state--error">
          Cette tâche n'a pas encore de référence commune. Tant qu'elle
          n'existe pas, aucune exécution n'est possible : rclone ne peut pas
          savoir ce qui a changé de chaque côté depuis la dernière fois.
        </p>
      )}

      <p>
        L'initialisation <strong>fusionne les deux côtés</strong> : ce qui
        n'existe que d'un bord est copié vers l'autre, et rien n'est supprimé.
        Simulez-la d'abord, lisez ce qu'elle annonce, puis appliquez-la.
      </p>

      <ol className="bisync__steps">
        <li className={settings.resync_simulated ? "done" : undefined}>
          <button
            type="button"
            className="btn btn--small"
            disabled={occupe}
            onClick={onSimulateResync}
          >
            1. Simuler l'initialisation
          </button>
          {settings.resync_simulated && <span className="chip">simulée</span>}
        </li>
        <li className={settings.initialised ? "done" : undefined}>
          <button
            type="button"
            className="btn btn--small btn--danger"
            disabled={occupe || !settings.resync_simulated}
            onClick={onApplyResync}
          >
            2. Appliquer l'initialisation
          </button>
          {settings.initialised && (
            <span className="chip">
              référence établie
              {settings.initialised_at
                ? ` le ${new Date(settings.initialised_at).toLocaleString("fr-FR")}`
                : ""}
            </span>
          )}
        </li>
      </ol>

      {!settings.resync_simulated && !settings.initialised && (
        <p className="field__help">
          Le second bouton reste inactif tant que la simulation n'a pas été
          lancée : une fusion se lit avant de s'exécuter.
        </p>
      )}

      <label className="field">
        <span className="field__label">En cas de conflit</span>
        <select
          value={settings.conflict_resolve}
          disabled={occupe}
          onChange={(event) =>
            void act(() =>
              updateBisync(taskId, { conflict_resolve: event.target.value }),
            )
          }
        >
          {Object.entries(RESOLVE_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>

      {settings.conflict_resolve === "none" && (
        <p className="field__help">
          Un fichier modifié des deux côtés est conservé en double, sous les
          noms <code>…conflict1</code> et <code>…conflict2</code>.{" "}
          <strong>Le nom d'origine disparaît alors des deux côtés</strong> :
          rien n'est perdu, mais c'est à vous de choisir la bonne version et
          de la renommer.
        </p>
      )}

      <label className="toggle">
        <input
          type="checkbox"
          checked={settings.check_access}
          disabled={occupe}
          onChange={(event) =>
            void act(() =>
              updateBisync(taskId, { check_access: event.target.checked }),
            )
          }
        />
        Exiger un fichier témoin des deux côtés avant chaque exécution
      </label>

      {settings.check_access && (
        <>
          <p className="field__help">
            Protège du cas où un côté répond mais ne contient plus rien — un
            partage démonté, par exemple. Les témoins doivent exister avant la
            première exécution.
          </p>
          <div className="actions">
            <button
              type="button"
              className="btn btn--small btn--ghost"
              disabled={occupe}
              onClick={() =>
                void act(
                  () => placeAccessMarkers(taskId),
                  `Témoins déposés des deux côtés pour « ${taskName} ».`,
                )
              }
            >
              Déposer les témoins
            </button>
          </div>
        </>
      )}

      {notice && <p className="state">{notice}</p>}
      {error && <p className="state state--error">{error}</p>}
    </div>
  );
}
