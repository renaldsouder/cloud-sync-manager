import { useEffect, useState } from "react";

import { fetchReleaseNotes, type Release } from "./api";

/** Dernière version dont l'utilisateur a vu les nouveautés. */
const SEEN_KEY = "csm.release-notes.seen";

function readSeen(): string | null {
  try {
    return window.localStorage.getItem(SEEN_KEY);
  } catch {
    return null;
  }
}

function writeSeen(version: string) {
  try {
    window.localStorage.setItem(SEEN_KEY, version);
  } catch {
    /* navigation privée : le bandeau reviendra, rien de grave */
  }
}

function titre(release: Release): string {
  if (!release.version) return "En développement";
  const date = release.date
    ? new Date(`${release.date}T12:00:00`).toLocaleDateString("fr-FR", {
        dateStyle: "long",
      })
    : "";
  return `Version ${release.version}${date ? ` — ${date}` : ""}`;
}

function Bloc({ release }: { release: Release }) {
  return (
    <div className="notes__release">
      <h3>{titre(release)}</h3>
      {release.important.length > 0 && (
        <ul className="notes__important">
          {release.important.map((ligne) => (
            <li key={ligne}>{ligne}</li>
          ))}
        </ul>
      )}
      {release.highlights.length > 0 && (
        <ul>
          {release.highlights.map((ligne) => (
            <li key={ligne}>{ligne}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Notes de version (UPDATE-004), dans l'onglet État.
 *
 * Le bloc « En développement » n'apparaît que sur une image construite
 * entre deux versions : il décrit ce qui tourne réellement.
 */
export default function ReleaseNotes() {
  const [releases, setReleases] = useState<Release[] | null>(null);
  const [toutes, setToutes] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetchReleaseNotes(controller.signal)
      .then((data) => {
        setReleases(data.releases);
        writeSeen(data.current);
      })
      .catch(() => {
        /* les notes sont un confort : leur absence n'empêche rien */
      });
    return () => controller.abort();
  }, []);

  if (!releases) return null;
  const visibles = toutes ? releases : releases.slice(0, 2);

  return (
    <section className="card notes">
      <h2>Nouveautés</h2>
      {visibles.map((release) => (
        <Bloc key={release.version ?? "dev"} release={release} />
      ))}
      {releases.length > visibles.length && (
        <button
          type="button"
          className="btn btn--small btn--ghost"
          onClick={() => setToutes(true)}
        >
          Versions précédentes
        </button>
      )}
    </section>
  );
}

/**
 * Bandeau affiché une fois après une mise à jour : la version a changé
 * depuis la dernière visite. Silencieux à la toute première visite — une
 * installation neuve n'a rien « de nouveau » à annoncer.
 */
export function UpdateBanner({
  version,
  onVoir,
}: {
  version: string | undefined;
  onVoir: () => void;
}) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!version) return;
    const seen = readSeen();
    if (seen === null) {
      writeSeen(version);
    } else if (seen !== version) {
      setVisible(true);
    }
  }, [version]);

  if (!visible || !version) return null;
  return (
    <div className="notice">
      <span>
        Cloud Sync Manager est passé en version <strong>{version}</strong>.
      </span>
      <span className="actions">
        <button
          type="button"
          className="btn btn--small"
          onClick={() => {
            setVisible(false);
            onVoir();
          }}
        >
          Voir les nouveautés
        </button>
        <button
          type="button"
          className="btn btn--small btn--ghost"
          onClick={() => {
            writeSeen(version);
            setVisible(false);
          }}
        >
          Masquer
        </button>
      </span>
    </div>
  );
}
