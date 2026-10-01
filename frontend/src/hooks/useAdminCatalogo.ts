import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { MonitoraggioCatalogo } from "../types/monitoraggio";

/** Il backend aspetta il monitoraggio al massimo 10 s: qui un margine sopra. */
const MONITORAGGIO_TIMEOUT_MS = 15_000;
/** Il riepilogo si ricalcola ogni 15 minuti circa e il backend tiene ogni esito
 *  in cache per 60 s: rileggere più spesso non porta dati nuovi. Con la scheda
 *  del browser in secondo piano la rilettura si ferma (default di TanStack). */
const MONITORAGGIO_INTERVALLO_MS = 60_000;

/** Nessun retry sui 4xx (401/403: non cambiano riprovando). */
const senzaRetry4xx = (tentativi: number, errore: unknown) => {
  const status = (errore as { response?: { status?: number } } | null)?.response?.status;
  return !(status && status >= 400 && status < 500) && tentativi < 2;
};

/** Monitoraggio del catalogo bandi per il pannello admin «Catalogo» (rotta e
 *  menu solo per gli admin; lo verifica anche il server). Gli esiti della
 *  chiamata al catalogo (chiave mancante o non valida, rete…) arrivano come
 *  `stato_accesso` in una risposta 200: un errore qui è del nostro backend. */
export function useMonitoraggioCatalogo() {
  return useQuery({
    queryKey: ["admin", "catalogo", "monitoraggio"],
    queryFn: async () =>
      (
        await api.get<MonitoraggioCatalogo>("/admin/catalogo/monitoraggio", {
          timeout: MONITORAGGIO_TIMEOUT_MS,
        })
      ).data,
    refetchInterval: MONITORAGGIO_INTERVALLO_MS,
    retry: senzaRetry4xx,
  });
}
