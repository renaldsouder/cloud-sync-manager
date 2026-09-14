import { useCallback, useEffect, useState } from "react";

import { browseLocal, type Listing } from "./api";

type Props = {
  valeur: string;
  /** Une destination ne peut pas être appdata, system ou domains (P13). */
  destination?: boolean;
  onChoisir: (chemin: string) => void;
  onFermer: () => void;
};

/**
 * Sélection d'un dossier local (LOCAL-002, §3).
 *
 * Taper un chemin à l'aveugle ne dit pas s'il existe, s'il est lisible, ni
 * s'il s'agit d'un share que l'application refusera. Ce parcours le montre
 * avant la validation plutôt qu'après.
 *
 * Les shares interdits en destination restent affichés, grisés : les masquer
 * laisserait croire qu'ils n'existent pas, et l'utilisateur les chercherait.
 */
export default function LocalPicker({
  valeur,
  destination = false,
  onChoisir,
  onFermer,
}: Props) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chemin, setChemin] = useState<string | undefined>(valeur || undefined);

  const charger = useCallback(
    async (cible?: string, replier = false) => {
      try {
        const recu = await browseLocal(cible);
        setListing(recu);
        setChemin(recu.path);
        setError(null);
      } catch (cause: unknown) {
        // Le champ peut contenir un chemin encore inexistant — c'est même le
        // cas au premier usage, où il vaut « /mnt/user/ » par défaut. Ouvrir
        // l'explorateur sur une erreur au lieu de la racine serait absurde :
        // on vient précisément d'y chercher le bon chemin.
        if (replier) {
          void charger(undefined);
          return;
        }
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    },
    [],
  );

  useEffect(() => {
    void charger(valeur || undefined, true);
    // Volontairement monté une fois : la navigation se pilote ensuite par
    // les clics, pas par la valeur du champ.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error) {
    return (
      <div className="notice">
        <p className="state state--error">{error}</p>
        <div className="actions">
          <button type="button" className="btn btn--small btn--ghost" onClick={onFermer}>
            Fermer
          </button>
        </div>
      </div>
    );
  }

  if (!listing) return <p className="state">Lecture des dossiers…</p>;

  return (
    <div className="notice">
      <strong>Choisir un dossier</strong>
      <p className="path">
        <code>{listing.path}</code>
      </p>

      <div className="actions">
        {listing.parent && (
          <button
            type="button"
            className="btn btn--small btn--ghost"
            onClick={() => void charger(listing.parent ?? undefined)}
          >
            ↑ Dossier parent
          </button>
        )}
        <button
          type="button"
          className="btn btn--small"
          onClick={() => onChoisir(listing.path)}
        >
          Choisir ce dossier
        </button>
        <button type="button" className="btn btn--small btn--ghost" onClick={onFermer}>
          Annuler
        </button>
      </div>

      {listing.entries.length === 0 && (
        <p className="state">Ce dossier ne contient aucun sous-dossier.</p>
      )}

      <ul className="dossiers">
        {listing.entries.map((entree) => {
          const interdit = destination && entree.forbidden_destination;
          return (
            <li key={entree.path}>
              <button
                type="button"
                className="dossiers__ligne"
                disabled={interdit}
                onClick={() => void charger(entree.path)}
              >
                <span className="dossiers__nom">{entree.name}</span>
                {interdit && (
                  <span className="field__help">
                    ne peut pas être une destination
                  </span>
                )}
                {!interdit && !entree.writable && (
                  <span className="field__help">lecture seule</span>
                )}
              </button>
            </li>
          );
        })}
      </ul>

      {listing.truncated && (
        <p className="state state--error">
          Ce dossier contient trop de sous-dossiers pour être listé
          entièrement. Saisissez le chemin directement.
        </p>
      )}

      {chemin !== valeur && (
        <p className="field__help">
          Cliquez sur «&nbsp;Choisir ce dossier&nbsp;» pour retenir{" "}
          <code>{listing.path}</code>.
        </p>
      )}
    </div>
  );
}
