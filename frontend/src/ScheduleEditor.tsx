import { useState } from "react";

import { updateTask, type Schedule, type Task } from "./api";

type Props = {
  task: Task;
  onSaved: () => void;
  onCancel: () => void;
};

const DAYS: [number, string][] = [
  [1, "L"],
  [2, "M"],
  [3, "M"],
  [4, "J"],
  [5, "V"],
  [6, "S"],
  [7, "D"],
];

/**
 * Planification sans cron (PLAN-002, PLAN-003, §11).
 *
 * Le §11 est net : le parcours standard ne doit pas demander d'écrire une
 * expression cron. On propose donc ce que l'utilisateur a en tête, et la
 * politique de rattrapage est un choix explicite plutôt qu'un réglage subi.
 */
export default function ScheduleEditor({ task, onSaved, onCancel }: Props) {
  const current = task.schedule;
  const [kind, setKind] = useState<Schedule["kind"]>(current?.kind ?? "manual");
  const [minutes, setMinutes] = useState(String(current?.minutes ?? 60));
  const [at, setAt] = useState(current?.time ?? "02:30");
  const [days, setDays] = useState<number[]>(current?.days ?? [1, 2, 3, 4, 5]);
  const [catchUp, setCatchUp] = useState<Schedule["catch_up"]>(current?.catch_up ?? "skip");
  const [cron, setCron] = useState(current?.cron ?? "30 2 * * *");
  const [quiet, setQuiet] = useState(Boolean(current?.quiet));
  const [quietStart, setQuietStart] = useState(current?.quiet?.start ?? "08:00");
  const [quietEnd, setQuietEnd] = useState(current?.quiet?.end ?? "18:00");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function toggleDay(day: number) {
    setDays((previous) =>
      previous.includes(day)
        ? previous.filter((value) => value !== day)
        : [...previous, day].sort(),
    );
  }

  async function save() {
    setBusy(true);
    setError(null);
    const schedule: Schedule = { kind, catch_up: catchUp };
    if (kind === "interval") schedule.minutes = Number(minutes);
    if (kind === "daily" || kind === "weekly") schedule.time = at;
    if (kind === "weekly") schedule.days = days;
    if (kind === "cron") schedule.cron = cron.trim();
    if (kind !== "manual" && quiet) schedule.quiet = { start: quietStart, end: quietEnd };

    try {
      await updateTask(task.id, { schedule });
      onSaved();
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="schedule">
      <fieldset className="choices">
        <legend className="field__label">Quand lancer cette tâche</legend>
        {(
          [
            ["manual", "Manuellement seulement"],
            ["interval", "À intervalle régulier"],
            ["daily", "Tous les jours à heure fixe"],
            ["weekly", "Certains jours de la semaine"],
            ["cron", "Expression cron (avancé)"],
          ] as [Schedule["kind"], string][]
        ).map(([value, label]) => (
          <label className="choice" key={value}>
            <input
              type="radio"
              name={`kind-${task.id}`}
              checked={kind === value}
              onChange={() => setKind(value)}
            />
            <span>{label}</span>
          </label>
        ))}
      </fieldset>

      {kind === "interval" && (
        <label className="field">
          <span className="field__label">Intervalle, en minutes</span>
          <input
            type="number"
            min={1}
            value={minutes}
            onChange={(event) => setMinutes(event.target.value)}
          />
        </label>
      )}

      {(kind === "daily" || kind === "weekly") && (
        <label className="field">
          <span className="field__label">Heure</span>
          <input type="time" value={at} onChange={(event) => setAt(event.target.value)} />
          <span className="field__help">
            Heure locale du serveur, telle que l'affiche Unraid.
          </span>
        </label>
      )}

      {kind === "weekly" && (
        <div className="field">
          <span className="field__label">Jours</span>
          <div className="days">
            {DAYS.map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={days.includes(value) ? "day day--on" : "day"}
                onClick={() => toggleDay(value)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      )}

      {kind === "cron" && (
        <label className="field">
          <span className="field__label">Expression cron</span>
          <input
            value={cron}
            spellCheck={false}
            onChange={(event) => setCron(event.target.value)}
          />
          <span className="field__help">
            Cinq champs : minute, heure, jour du mois, mois, jour de la semaine
            (0 ou 7 = dimanche). Par exemple <code>*/15 9-17 * * 1-5</code> :
            toutes les quinze minutes, de 9&nbsp;h à 17&nbsp;h, en semaine. Ni
            noms de jours ni raccourcis <code>@daily</code>.
          </span>
        </label>
      )}

      {kind !== "manual" && (
        <>
          <label className="toggle">
            <input
              type="checkbox"
              checked={quiet}
              onChange={(event) => setQuiet(event.target.checked)}
            />
            Ne jamais démarrer pendant une plage horaire
          </label>
          {quiet && (
            <div className="advanced__grid">
              <label className="field">
                <span className="field__label">De</span>
                <input
                  type="time"
                  value={quietStart}
                  onChange={(event) => setQuietStart(event.target.value)}
                />
              </label>
              <label className="field">
                <span className="field__label">À</span>
                <input
                  type="time"
                  value={quietEnd}
                  onChange={(event) => setQuietEnd(event.target.value)}
                />
              </label>
            </div>
          )}
          {quiet && (
            <p className="state">
              Une exécution prévue dans cette plage part à sa fin. Une exécution
              déjà en cours n'est pas interrompue : pour la ralentir, utilisez une
              limite de bande passante selon l'heure dans «&nbsp;Modifier&nbsp;».
              Un lancement manuel reste possible à tout moment.
            </p>
          )}
        </>
      )}

      {kind !== "manual" && (
        <label className="toggle">
          <input
            type="checkbox"
            checked={catchUp === "once"}
            onChange={(event) => setCatchUp(event.target.checked ? "once" : "skip")}
          />
          Rattraper une exécution manquée au redémarrage du serveur — une seule fois,
          jamais toutes celles qui ont été sautées.
        </label>
      )}

      {error && <p className="state state--error">{error}</p>}

      <div className="actions">
        <button type="button" className="btn btn--small" disabled={busy} onClick={save}>
          {busy ? "Enregistrement…" : "Enregistrer"}
        </button>
        <button type="button" className="btn btn--small btn--ghost" onClick={onCancel}>
          Annuler
        </button>
      </div>
    </div>
  );
}
