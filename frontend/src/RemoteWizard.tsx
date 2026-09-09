import { useEffect, useMemo, useState } from "react";

import { createRemote, fetchProviders, type Provider } from "./api";

type Props = {
  onCreated: () => void;
  onCancel: () => void;
};

/**
 * Assistant d'ajout d'un stockage (FIRST-002, CLOUD-003).
 *
 * Les champs sont construits à partir du catalogue rclone : rien n'est codé
 * en dur, un fournisseur ajouté par une future version apparaît tout seul.
 */
export default function RemoteWizard({ onCreated, onCancel }: Props) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [includeAll, setIncludeAll] = useState(false);
  // Les fournisseurs OAuth — Drive, OneDrive, Dropbox — rangent `token`,
  // `drive_id` et `drive_type` parmi les options avancées de rclone. Sans
  // ce commutateur, ils sont impossibles à configurer.
  const [advanced, setAdvanced] = useState(false);
  const [chosenName, setChosenName] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetchProviders(includeAll, advanced, controller.signal)
      .then(setProviders)
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      });
    return () => controller.abort();
  }, [includeAll, advanced]);

  // Dérivé de la liste plutôt que mémorisé : basculer les options avancées
  // recharge le catalogue, et le fournisseur choisi doit suivre.
  const chosen = providers.find((provider) => provider.name === chosenName) ?? null;

  const missing = useMemo(
    () =>
      (chosen?.options ?? [])
        .filter((option) => option.required && !values[option.name]?.trim())
        .map((option) => option.name),
    [chosen, values],
  );

  const canSubmit = Boolean(chosen) && name.trim().length > 0 && missing.length === 0;

  async function submit() {
    if (!chosen || !canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      await createRemote({ name: name.trim(), provider: chosen.name, options: values });
      onCreated();
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  if (!chosen) {
    return (
      <section className="card">
        <div className="card__head">
          <h2>Choisir un fournisseur</h2>
          <button type="button" className="btn btn--ghost" onClick={onCancel}>
            Annuler
          </button>
        </div>

        {error && <p className="state state--error">{error}</p>}

        <ul className="providers">
          {providers.map((provider) => (
            <li key={provider.name}>
              <button
                type="button"
                className="provider"
                onClick={() => {
                  setChosenName(provider.name);
                  setName(provider.label);
                }}
              >
                <span className="provider__label">{provider.label}</span>
                {provider.needs_oauth && (
                  <span className="badge">autorisation navigateur</span>
                )}
              </button>
            </li>
          ))}
        </ul>

        <label className="toggle">
          <input
            type="checkbox"
            checked={includeAll}
            onChange={(event) => setIncludeAll(event.target.checked)}
          />
          Afficher tous les fournisseurs pris en charge par rclone
        </label>
      </section>
    );
  }

  return (
    <section className="card">
      <div className="card__head">
        <h2>{chosen.label}</h2>
        <button type="button" className="btn btn--ghost" onClick={() => setChosenName(null)}>
          Changer de fournisseur
        </button>
      </div>

      {chosen.needs_oauth && (
        <div className="notice">
          <strong>Ce fournisseur demande une autorisation par navigateur.</strong>
          <p>
            Lancez cette commande <em>sur votre ordinateur</em> — pas sur le serveur
            Unraid — puis collez le jeton obtenu dans le champ <code>token</code> :
          </p>
          <pre>rclone authorize "{chosen.name}"</pre>
          <p>
            Cochez ensuite « afficher les options avancées » ci-dessous pour faire
            apparaître le champ <code>token</code>.
            {chosen.name === "onedrive" && (
              <>
                {" "}
                OneDrive demande aussi <code>drive_id</code> et{" "}
                <code>drive_type</code>, que la commande affiche.
              </>
            )}
          </p>
        </div>
      )}

      <label className="field">
        <span className="field__label">Nom du stockage</span>
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Google Drive perso"
        />
        <span className="field__help">
          Nom affiché dans l'application. Vous pourrez le modifier plus tard.
        </span>
      </label>

      <label className="toggle">
        <input
          type="checkbox"
          checked={advanced}
          onChange={(event) => setAdvanced(event.target.checked)}
        />
        Afficher les options avancées. Nécessaire pour Google Drive, OneDrive et
        Dropbox, dont le champ <code>token</code> en fait partie.
      </label>

      {chosen.options.map((option) => (
        <label className="field" key={option.name}>
          <span className="field__label">
            {option.name}
            {option.required && <span className="field__required"> obligatoire</span>}
          </span>
          <input
            type={option.is_password ? "password" : "text"}
            value={values[option.name] ?? ""}
            placeholder={option.default ?? ""}
            autoComplete={option.is_password ? "new-password" : "off"}
            onChange={(event) =>
              setValues((previous) => ({
                ...previous,
                [option.name]: event.target.value,
              }))
            }
          />
          {option.help && <span className="field__help">{option.help}</span>}
        </label>
      ))}

      {error && <p className="state state--error">{error}</p>}

      <div className="actions">
        <button type="button" className="btn" disabled={!canSubmit || busy} onClick={submit}>
          {busy ? "Création…" : "Créer le stockage"}
        </button>
        <button type="button" className="btn btn--ghost" onClick={onCancel}>
          Annuler
        </button>
      </div>
    </section>
  );
}
