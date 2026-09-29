import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiErrorCode } from "../lib/api";
import type {
  CallBacheca,
  FiltriBacheca,
  Page,
  PartnerEmailSettings,
  PartnerSuggerito,
  PerTePage,
  RiepilogoPartenariati,
} from "../types";
import { useActiveCompany } from "./useActiveCompany";
import { useAuth } from "./useAuth";
import { callKey, callListaKey } from "./useCallPartenariato";
import { useFunzioni } from "./useFunzioni";

/* Scoperta delle call (WP6): «Per te», bacheca, salvate, suggeriti per il
 * proponente, riepilogo per il badge del menu, preferenze email (il
 * confronto della propria azienda con una call arriva con il dettaglio della
 * call, `useCall`). Radice unica `["partenariati", …]`: le
 * risposte dipendono dall'azienda attiva (chi guarda), che entra nella
 * chiave; al cambio d'azienda la cache si svuota comunque. */

/** Righe per pagina di bacheca e salvate (come i suggeriti del server). */
export const BACHECA_PAGINA = 20;
/** Il badge del menu si aggiorna da solo ogni minuto (solo a scheda visibile:
 *  TanStack non ripete in background). */
const RIEPILOGO_POLLING_MS = 60_000;

export const perTeKey = (aziendaId: string | null, page: number) =>
  ["partenariati", "per-te", aziendaId, page] as const;
const PER_TE_ROOT = ["partenariati", "per-te"] as const;
export const riepilogoKey = (aziendaId: string | null) =>
  ["partenariati", "riepilogo", aziendaId] as const;
const RIEPILOGO_ROOT = ["partenariati", "riepilogo"] as const;
const LISTE_ROOT = ["partenariati", "call", "lista"] as const;
export const suggeritiKey = (id: string | undefined, aziendaId: string | null, page: number) =>
  ["partenariati", "call", id, aziendaId, "suggeriti", page] as const;
/** Preferenze dell'UTENTE (non dell'azienda): nessun id d'azienda. */
const EMAIL_SETTINGS_KEY = ["partenariati", "email-settings"] as const;

/** Mentre arriva un'altra pagina resta visibile la precedente, ma SOLO della
 *  stessa lista (stessa azienda, stessa call, stessi filtri: la chiave meno
 *  l'ultimo elemento, la pagina). Mai i dati di un'altra azienda o call. */
function stessaListaAltraPagina(chiave: readonly unknown[]) {
  const base = JSON.stringify(chiave.slice(0, -1));
  return <T>(precedenti: T | undefined, query: { queryKey: readonly unknown[] } | undefined) =>
    query && JSON.stringify(query.queryKey.slice(0, -1)) === base ? precedenti : undefined;
}

/** Il 404 (modulo spento, call non visibile) e il 409 dell'azienda mancante
 *  non cambiano riprovando. */
const retry = (tentativi: number, errore: unknown) => {
  const codice = apiErrorCode(errore);
  return codice !== "not_found" && codice !== "azienda_mancante" && tentativi < 2;
};

// ---- Letture -------------------------------------------------------------------

/** «Per te»: le call adatte all'azienda attiva, anche senza visibilità come
 *  partner (`opt_in: false`). Anche i membri la leggono. */
export function usePerTe(page: number) {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  const { activeCompanyId } = useActiveCompany();
  const queryKey = perTeKey(activeCompanyId, page);
  return useQuery({
    queryKey,
    queryFn: async () =>
      (await api.get<PerTePage>("/partenariati/per-te", { params: { page } })).data,
    enabled: !!session && partenariatiAttivo,
    staleTime: 60_000,
    placeholderData: stessaListaAltraPagina(queryKey),
    retry,
  });
}

/** Parametri della richiesta: solo i filtri valorizzati. */
function parametriBacheca(filtri: FiltriBacheca) {
  const params: Record<string, string | number> = { ordine: filtri.ordine };
  if (filtri.bando) params.bando = filtri.bando;
  if (filtri.regione !== null) params.regione = filtri.regione;
  if (filtri.forma) params.forma = filtri.forma;
  if (filtri.ruolo) params.ruolo = filtri.ruolo;
  return params;
}

/** Bacheca (`vista=tutte`: call pubblicate e aperte a tutti, di altre
 *  aziende, con il confronto della tua se è idonea) e salvate
 *  (`vista=salvate`, senza filtri). */
export function useBacheca(vista: "tutte" | "salvate", filtri: FiltriBacheca, page: number) {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  const { activeCompanyId } = useActiveCompany();
  const params = vista === "tutte" ? parametriBacheca(filtri) : {};
  const queryKey = [...callListaKey(activeCompanyId, vista), params, page] as const;
  return useQuery({
    queryKey,
    queryFn: async () =>
      (
        await api.get<Page<CallBacheca>>("/partenariati/call", {
          params: { vista, ...params, page, page_size: BACHECA_PAGINA },
        })
      ).data,
    enabled: !!session && partenariatiAttivo,
    staleTime: 30_000,
    placeholderData: stessaListaAltraPagina(queryKey),
    retry,
  });
}

/** Aziende suggerite per una call (solo l'azienda che l'ha creata; i membri
 *  in lettura). Identificate da uno pseudonimo valido solo per questa call. */
export function useSuggeriti(id: string | undefined, page: number, abilitato = true) {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  const { activeCompanyId } = useActiveCompany();
  const queryKey = suggeritiKey(id, activeCompanyId, page);
  return useQuery({
    queryKey,
    queryFn: async () =>
      (
        await api.get<Page<PartnerSuggerito>>(`/partenariati/call/${id}/suggeriti`, {
          params: { page },
        })
      ).data,
    enabled: !!session && partenariatiAttivo && !!id && abilitato,
    staleTime: 60_000,
    placeholderData: stessaListaAltraPagina(queryKey),
    retry,
  });
}

/** Numeri per il badge del link «Partenariati» nel menu. */
export function useRiepilogoPartenariati() {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  const { activeCompanyId } = useActiveCompany();
  return useQuery({
    queryKey: riepilogoKey(activeCompanyId),
    queryFn: async () =>
      (await api.get<RiepilogoPartenariati>("/partenariati/riepilogo")).data,
    enabled: !!session && partenariatiAttivo,
    staleTime: 30_000,
    refetchInterval: RIEPILOGO_POLLING_MS,
    retry,
  });
}

// ---- Scritture -----------------------------------------------------------------

interface SalvaInput {
  id: string;
  salva: boolean;
}

/** Salva (e segui) o togli dalle salvate una call di un'altra azienda. Solo
 *  il titolare. Dopo l'esito si rileggono liste, «Per te», riepilogo e il
 *  dettaglio della call (che porta lo stato «salvata»). */
export function useSalvaCall() {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: ({ id }: SalvaInput) => ({ azienda: activeCompanyId, id }),
    mutationFn: async ({ id, salva }: SalvaInput) => {
      if (salva) await api.post(`/partenariati/call/${id}/salva`);
      else await api.delete(`/partenariati/call/${id}/salva`);
    },
    onSettled: (_dati, _errore, _variabili, avvio) => {
      queryClient.invalidateQueries({ queryKey: LISTE_ROOT });
      queryClient.invalidateQueries({ queryKey: PER_TE_ROOT });
      queryClient.invalidateQueries({ queryKey: RIEPILOGO_ROOT });
      if (avvio) {
        queryClient.invalidateQueries({ queryKey: callKey(avvio.id, avvio.azienda), exact: true });
      }
    },
  });
}

// ---- Preferenze email (per utente) -----------------------------------------------

export function usePartnerEmailSettings() {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  return useQuery({
    queryKey: EMAIL_SETTINGS_KEY,
    queryFn: async () =>
      (await api.get<PartnerEmailSettings>("/me/partenariati/email-settings")).data,
    enabled: !!session && partenariatiAttivo,
    staleTime: 60_000,
    retry,
  });
}

/** Salva entrambe le scelte (la risposta aggiorna la cache). */
export function useSalvaPartnerEmailSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (dati: PartnerEmailSettings) =>
      (await api.put<PartnerEmailSettings>("/me/partenariati/email-settings", dati)).data,
    onSuccess: (salvate) => queryClient.setQueryData(EMAIL_SETTINGS_KEY, salvate),
  });
}
