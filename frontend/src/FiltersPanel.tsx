import { useCallback, useEffect, useState } from "react";

import {
  createFilterSet,
  deleteFilterSet,
  fetchFilterSets,
  previewFilterSet,
  updateFilterSet,
  type FilterPreview,
  type FilterRule,
  type FilterSet,
} from "./api";

const RULE_LABELS: [string, string][] = [
  ["exclude_path", "Exclure un dossier"],
  ["include_path", "N'inclure qu'un dossier"],
  ["exclude_ext", "Exclure une extension"],
  ["include_ext", "N'inclure qu'une extension"],
  ["exclude_name", "Exclure un motif de nom"],
  ["include_name", "N'inclure qu'un motif de nom"],
  ["hidden", "Exclure les fichiers cachés"],
  ["min_size", "Taille minimale"],
  ["max_size", "Taille maximale"],
];

const PLACEHOLDERS: Record<string, string> = {
  exclude_path: "Cache",
  include_path: "Photos/2026",
  exclude_ext: "tmp",
  include_ext: "jpg",
  exclude_name: "*.partial",
  include_name: "IMG_*",
  hidden: "exclude",
  min_size: "10M",
  max_size: "2G",
};

/** Filtres et outil de test (FILT-001 → FILT-005, §12). */
export default function FiltersPanel() {
  const [sets, setSets] = useState<FilterSet[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [rules, setRules] = useState<FilterRule[]>([]);
  const [previewPath, setPreviewPath] = useState("/mnt/user/");
  const [preview, setPreview] = useState<FilterPreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setSets(await fetchFilterSets());
      setError(null);
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  function edit(item: FilterSet | null) {
    setPreview(null);
    setEditing(item ? item.id : "__new__");
    setName(item ? item.name : "");
    setRules(item ? item.rules : []);
  }

  async function save() {
    setError(null);
    try {
      if (editing === "__new__") await createFilterSet({ name, rules });
      else if (editing) await updateFilterSet(editing, { name, rules });
      setEditing(null);
      await reload();
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function runPreview() {
    if (!editing || editing === "__new__") return;
    setError(null);
    try {
      setPreview(await previewFilterSet(editing, previewPath));
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  return (
    <section className="card">
      <div className="card__head">
        <h2>Filtres</h2>
        <button type="button" className="btn" onClick={() => edit(null)}>
          Nouveau jeu
        </button>
      </div>

      {error && <p className="state state--error">{error}</p>}

      {editing === null && (
        <ul className="remotes">
          {sets.length === 0 && (
            <li className="state">
              Aucun jeu de filtres. Sans filtre, tout le contenu du dossier est
              synchronisé.
            </li>
          )}
          {sets.map((item) => (
            <li key={item.id} className="remote">
              <div className="remote__head">
                <span className="remote__name">{item.name}</span>
                <span className="remote__provider">{item.rules.length} règle(s)</span>
                <span className="remote__status">
                  {item.task_count > 0 ? `${item.task_count} tâche(s)` : "inutilisé"}
                </span>
              </div>
              <p className="path">
                {item.compiled.map((line) => (
                  <code key={line}>{line}</code>
                ))}
              </p>
              <div className="actions">
                <button
                  type="button"
                  className="btn btn--small btn--ghost"
                  onClick={() => edit(item)}
                >
                  Modifier et tester
                </button>
                {item.task_count === 0 && (
                  <button
                    type="button"
                    className="btn btn--small btn--ghost"
                    onClick={() =>
                      void deleteFilterSet(item.id).then(reload).catch((cause) =>
                        setError(cause instanceof Error ? cause.message : String(cause)),
                      )
                    }
                  >
                    Supprimer
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {editing !== null && (
        <>
          <label className="field">
            <span className="field__label">Nom du jeu</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Sans fichiers temporaires"
            />
          </label>

          <p className="field__help">
            Les règles sont appliquées dans l'ordre et la première qui correspond
            l'emporte. Dès qu'une règle d'inclusion existe, tout ce qui n'est pas
            explicitement inclus est écarté.
          </p>

          <ul className="rules">
            {rules.map((rule, index) => (
              <li key={index}>
                <select
                  value={rule.type}
                  onChange={(event) =>
                    setRules(
                      rules.map((current, position) =>
                        position === index
                          ? { ...current, type: event.target.value }
                          : current,
                      ),
                    )
                  }
                >
                  {RULE_LABELS.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
                <input
                  value={rule.value}
                  placeholder={PLACEHOLDERS[rule.type] ?? ""}
                  onChange={(event) =>
                    setRules(
                      rules.map((current, position) =>
                        position === index
                          ? { ...current, value: event.target.value }
                          : current,
                      ),
                    )
                  }
                />
                <button
                  type="button"
                  className="btn btn--small btn--ghost"
                  onClick={() => setRules(rules.filter((_, position) => position !== index))}
                >
                  Retirer
                </button>
              </li>
            ))}
          </ul>

          <div className="actions">
            <button
              type="button"
              className="btn btn--small btn--ghost"
              onClick={() => setRules([...rules, { type: "exclude_path", value: "" }])}
            >
              Ajouter une règle
            </button>
            <button type="button" className="btn btn--small" onClick={() => void save()}>
              Enregistrer
            </button>
            <button
              type="button"
              className="btn btn--small btn--ghost"
              onClick={() => setEditing(null)}
            >
              Fermer
            </button>
          </div>

          {editing !== "__new__" && (
            <>
              <label className="field">
                <span className="field__label">Tester sur un dossier</span>
                <input
                  value={previewPath}
                  onChange={(event) => setPreviewPath(event.target.value)}
                  placeholder="/mnt/user/Photos"
                />
                <span className="field__help">
                  Enregistrez d'abord vos modifications : le test porte sur le jeu
                  tel qu'il est en base.
                </span>
              </label>
              <div className="actions">
                <button
                  type="button"
                  className="btn btn--small"
                  onClick={() => void runPreview()}
                >
                  Tester
                </button>
              </div>
            </>
          )}

          {preview && (
            <div className="preview">
              <h3>Seraient synchronisés — {preview.included.length}</h3>
              <ul className="blocked__paths">
                {preview.included.slice(0, 100).map((path) => (
                  <li key={path}>{path}</li>
                ))}
              </ul>
              <h3>Seraient écartés — {preview.excluded.length}</h3>
              <ul className="blocked__paths">
                {preview.excluded.slice(0, 100).map((item) => (
                  <li key={item.path}>
                    {item.path} <span className="muted">— {item.reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </section>
  );
}
