import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "./styles.css";
import { applyTheme, readTheme } from "./theme";

// Appliqué avant le premier rendu : pas d'éclair blanc à l'ouverture quand
// le thème sombre a été choisi (UI-006).
applyTheme(readTheme());

const container = document.getElementById("root");
if (!container) {
  throw new Error("Élément #root introuvable");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
