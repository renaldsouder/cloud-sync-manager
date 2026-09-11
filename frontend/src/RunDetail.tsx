import { useCallback, useEffect, useState } from "react";

import {
  fetchEventSummary,
  fetchEvents,
  formatBytes,
  type TaskEvent,
  type TaskEventSummary,
} from "./api";

type Props = {
  runId: string;
  onClose: () => void;
};

const PAGE = 200;

const KIND_LABEL: Record<string, string> = {
  transfer: "Transférés",
  delete: "Supprimés",
  skip_transfer: "Ignorés",
  skip_delete: "Suppressions évitées",
  conflict: "Conflits",
  error: "Erreurs",
  warning: "Avertissements",
  other: "Autres",
};

/** Ordre d'affichage des filtres : ce qui compte le plus d'abord. */
const KIND_ORDER = [
  "transfer",
  "delete",
  "conflict",
  "error",
  "skip_transfer",
  "skip_delete",
  "warning",
  "other",
];

/**
 * Détail fichier par fichier d'une exécution (LOG-002, §14 « Détaillé »).
 *
 * Les événements étaient collectés depuis toujours mais n'étaient lisibles
 * nulle part. La seule subtilité est la troncature : au-delà du plafond de
 * stockage, la liste est incomplète et doit le dire très visiblement — sans
 * quoi ne pas trouver un fichier ferait conclure qu'il n'est pas passé.
 */
export default function RunDetail({ runId, onClose }: Props) {
  const [summary, setSummary] = useState<TaskEventSummary | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [kind, setKind] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // La recherche part au repos de la frappe, pas à chaque touche.
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  const load = useCallback(
    async (signal: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const [counts, page] = await Promise.all([
          fetchEventSummary(runId, query || undefined, signal),
          fetchEvents(
            runId,
            { kind: kind ?? undefined, q: query || undefined, limit: PAGE },
            signal,
          ),
        ]);
        setSummary(counts);
        setEvents(page);
      } catch (cause: unknown) {
        if (signal.aborted) return;
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    },
    [runId, kind, query],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  async function loadMore() {
    setLoading(true);
    try {
      const next = await fetchEvents(runId, {
        kind: kind ?? undefined,
        q: query || undefined,
        offset: events.length,
        limit: PAGE,
      });
      setEvents((previous) => [...previous, ...next]);
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  const counts = summary?.counts ?? {};
  const matching = kind ? (counts[kind] ?? 0) : (summary?.total ?? 0);
  const kinds = KIND_ORDER.filter((value) => counts[value]);

  return (
    <div className="detail">
      <div className="detail__head">
        <strong>Détail de l'exécution</strong>
        <button type="button" className="btn btn--small btn--ghost" onClick={onClose}>
          Fermer
        </button>
      </div>

      {summary?.truncated && (
        <p className="state state--error">
          Cette liste est <strong>incomplète</strong> : le détail a été tronqué
          au-delà du plafond d'enregistrement. Les compteurs de l'exécution,
          eux, restent exacts — l'absence d'un fichier ci-dessous ne signifie
          donc pas qu'il n'a pas été traité.
        </p>
      )}

      <label className="field">
        <span className="field__label">Rechercher un chemin</span>
        <input
          value={search}
          placeholder="photos/2026"
          onChange={(event) => setSearch(event.target.value)}
        />
      </label>

      <div className="detail__filters">
        <button
          type="button"
          className={kind === null ? "chip chip--active" : "chip chip--button"}
          onClick={() => setKind(null)}
        >
          Tout ({summary?.total ?? 0})
        </button>
        {kinds.map((value) => (
          <button
            key={value}
            type="button"
            className={kind === value ? "chip chip--active" : "chip chip--button"}
            onClick={() => setKind(value)}
          >
            {KIND_LABEL[value] ?? value} ({counts[value]})
          </button>
        ))}
      </div>

      {error && <p className="state state--error">{error}</p>}

      {!error && events.length === 0 && !loading && (
        <p className="state">
          {query ? (
            <>Aucun fichier ne correspond à «&nbsp;{query}&nbsp;».</>
          ) : (
            <>
              Aucun détail pour cette exécution. Les fichiers inchangés ne sont
              pas journalisés : une exécution qui n'avait rien à faire n'a rien
              à détailler. Une simulation, elle, liste ce qu'elle aurait ignoré.
            </>
          )}
        </p>
      )}

      <ul className="detail__files">
        {events.map((event) => (
          <li key={event.id}>
            <span className={`tag tag--${event.kind}`}>
              {KIND_LABEL[event.kind] ?? event.kind}
            </span>
            <span className="detail__path">{event.path ?? event.message}</span>
            {event.size !== null && (
              <span className="detail__size">{formatBytes(event.size)}</span>
            )}
          </li>
        ))}
      </ul>

      {events.length < matching && (
        <button
          type="button"
          className="btn btn--small btn--ghost"
          disabled={loading}
          onClick={() => void loadMore()}
        >
          {loading
            ? "Chargement…"
            : `Afficher la suite (${events.length} sur ${matching})`}
        </button>
      )}
    </div>
  );
}
