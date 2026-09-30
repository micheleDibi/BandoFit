import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Solo GET, HEAD e OPTIONS passano dal proxy: la verifica visiva legge dati
// veri e non può scrivere nulla sull'API di produzione.
const METODI_SOLA_LETTURA = ["GET", "HEAD", "OPTIONS"];

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Solo per `npm run dev`: inoltra `/api` all'API di produzione in sola
  // lettura, così la verifica visiva usa dati veri con
  // `VITE_API_BASE_URL=/api/v1` in `.env.development.local` (il login lo fa
  // chi verifica). Vedi docs/frontend.md.
  server: {
    proxy: {
      "/api": {
        target: "https://bandofit.edunews24.it",
        changeOrigin: true,
        // `false` risponde 404 senza inoltrare: le scritture non partono.
        bypass: (req) => (METODI_SOLA_LETTURA.includes(req.method ?? "") ? undefined : false),
      },
    },
  },
  // `vite preview` erediterebbe il proxy di `server`: qui non ne ha nessuno.
  preview: {
    proxy: {},
  },
});
