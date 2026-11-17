/**
 * Thème clair, sombre ou automatique (UI-006).
 *
 * « Automatique » suit le réglage du système ou du navigateur — c'est le
 * défaut, et c'est aussi ce qui se rapproche le plus d'une « adaptation au
 * thème Unraid » : l'interface s'ouvre dans son propre onglet, sur son propre
 * port, et n'a aucun moyen fiable de lire le thème choisi dans le WebGUI
 * d'Unraid. Plutôt que de le deviner, on laisse l'utilisateur trancher.
 *
 * Le choix est mémorisé dans ce navigateur seulement : c'est une préférence
 * d'affichage, pas un réglage du serveur, et deux appareils peuvent vouloir
 * l'inverse l'un de l'autre.
 */

export type Theme = "auto" | "light" | "dark";

const CLE = "csm.theme";

export function readTheme(): Theme {
  try {
    const value = window.localStorage.getItem(CLE);
    return value === "light" || value === "dark" ? value : "auto";
  } catch {
    // Stockage indisponible (navigation privée, cookies bloqués) : on suit
    // le système, sans erreur visible.
    return "auto";
  }
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "auto") {
    delete root.dataset.theme;
  } else {
    root.dataset.theme = theme;
  }
}

export function saveTheme(theme: Theme): void {
  applyTheme(theme);
  try {
    if (theme === "auto") {
      window.localStorage.removeItem(CLE);
    } else {
      window.localStorage.setItem(CLE, theme);
    }
  } catch {
    // Le thème s'applique pour cette visite ; il ne sera simplement pas
    // retenu.
  }
}
