import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// En production, le build est servi par FastAPI sur le même port : aucune
// origine croisée, donc aucun CORS à configurer (P4).
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:3572" },
  },
});
