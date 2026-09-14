import { useCallback, useEffect, useState } from "react";

import { browseRemote, type RemoteListing } from "./api";

type Props = {
  remoteId: string;
  valeur: string;
  onChoisir: (chemin: string) => void;
  onFermer: () => void;
};

/**
 * Sélection d'un dossier distant (CLOUD-005, §3).
 *
 * Taper le nom d'un dossier distant sans filet a un coût particulier : une
 * faute de frappe ne se voit qu'à la première exécution, sur un dossier créé
 * par erreur — et côté Cloud, la corriger suppose de retrouver puis d'effacer
 * ce qui a été envoyé au mauvais endroit.
 */
export default function RemotePicker({
  remoteId,
  valeur,
  onChoisir,
  onFermer,
}: Props) {
  const [listing, setListing] = useState<RemoteListing | null>(null);
  const [error, setError] = useState<string | null>(null);

  const charger = useCallback(
    async (cible?: string, replier = false) => {
      try {
        const recu = await browseRemote(remoteId, cible);
        setListing(recu);
        setError(null);
      } catch (cause: unknown) {
        // Le champ peut désigner un dossier qui n'existe pas encore : la
        // tâche le créera. Ouvrir l'explorateur sur une erreur au lieu de la
        // racine serait absurde.
        if (replier) {
          void charger(undefined);
          return;
        }
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    },
    [remoteId],
  );

  useEffect(() => {
    void charger(valeur || undefined, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [remoteId]);

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

  if (!listing) return <p className="state">Lecture du stockage distant…</p>;

  return (
    <div className="notice">
      <strong>Choisir un dossier distant</strong>
      <p className="path">
        <code>{listing.path || "racine du stockage"}</code>
      </p>

      <div className="actions">
        {listing.parent !== null && (
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
        <p className="state">
          Aucun sous-dossier ici. Vous pouvez choisir ce dossier, ou saisir un
          nom qui n'existe pas encore : la tâche le créera.
        </p>
      )}

      <ul className="dossiers">
        {listing.entries.map((entree) => (
          <li key={entree.path}>
            <button
              type="button"
              className="dossiers__ligne"
              onClick={() => void charger(entree.path)}
            >
              <span className="dossiers__nom">{entree.name}</span>
            </button>
          </li>
        ))}
      </ul>

      {listing.truncated && (
        <p className="state state--error">
          Ce dossier contient trop de sous-dossiers pour être listé
          entièrement. Saisissez le chemin directement.
        </p>
      )}
    </div>
  );
}
