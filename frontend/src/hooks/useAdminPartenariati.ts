import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiErrorCode } from "../lib/api";
import type {
  AnteprimaStatement,
  CallAdmin,
  ContestoSegnalazione,
  CostiPartenariati,
  DecisioneRicorsoInput,
  DecisioneSegnalazioneInput,
  EsitoSospensione,
  EstrazioneAdmin,
  FiltroCodaSegnalazioni,
  FiltroIdentitaAdmin,
  IdentitaAdmin,
  IdentitaDecisioneInput,
  IdentitaEsito,
  IdentitaRevocaInput,
  MetrichePartenariati,
  OggettoModerazione,
  Page,
  PartenariatiRun,
  PartenariatoBando,
  SegnalazioneAdmin,
  StatoCall,
} from "../types";
import { useFunzioni } from "./useFunzioni";

/** Pannello admin dei partenariati (WP9). Radice `["admin", "partenariati",
 *  …]`: le rotte sono dietro il flag del modulo e riservate agli admin (lo
 *  verifica il server). Nessun retry sui 4xx: non cambiano riprovando. */
const ROOT = ["admin", "partenariati"] as const;
const SEGNALAZIONI = [...ROOT, "segnalazioni"] as const;
const CALL = [...ROOT, "call"] as const;
const IDENTITA = [...ROOT, "identita"] as const;
const ESTRAZIONI = [...ROOT, "estrazioni"] as const;
/** Contesto dei messaggi FUORI dalla radice: ogni lettura è un accesso
 *  registrato in audit, quindi nessuna invalidazione (azioni di moderazione,
 *  run dello scheduler) deve rileggerlo senza un'azione dell'admin. */
const contestoKey = (id: string) => ["admin-moderazione-contesto", id] as const;

const PAGE_SIZE = 20;
/** La run dello scheduler esegue tutti i passi del giorno (anche estrazioni a
 *  pagamento, nel budget giornaliero): può durare. */
const RUN_TIMEOUT_MS = 120_000;

const senzaRetry4xx = (tentativi: number, errore: unknown) => {
  const status = (errore as { response?: { status?: number } } | null)?.response?.status;
  return !(status && status >= 400 && status < 500) && tentativi < 2;
};

function useAttivo() {
  return useFunzioni().partenariatiAttivo;
}

// ---- Segnalazioni -------------------------------------------------------------

/** Coda delle segnalazioni: `aperte` (ricevute, in esame, con un ricorso da
 *  decidere) dalla più vecchia, uno stato, o `tutte`. */
export function useAdminSegnalazioni(stato: FiltroCodaSegnalazioni, page: number) {
  const attivo = useAttivo();
  const params: Record<string, string | number> = { stato, page, page_size: PAGE_SIZE };
  return useQuery({
    queryKey: [...SEGNALAZIONI, params],
    queryFn: async () =>
      (await api.get<Page<SegnalazioneAdmin>>("/admin/partenariati/segnalazioni", { params })).data,
    enabled: attivo,
    placeholderData: keepPreviousData,
    retry: senzaRetry4xx,
  });
}

/** Dopo ogni azione di moderazione: coda, elenco call (sospensioni) e il
 *  dettaglio che l'admin sta guardando si rileggono. */
function useInvalidaModerazione() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: SEGNALAZIONI });
    void queryClient.invalidateQueries({ queryKey: CALL });
  };
}

export function usePrendiSegnalazione() {
  const invalida = useInvalidaModerazione();
  return useMutation({
    mutationFn: async (id: string) =>
      (await api.post<SegnalazioneAdmin>(`/admin/partenariati/segnalazioni/${id}/prendi`)).data,
    onSettled: invalida,
  });
}

export function useDecidiSegnalazione() {
  const invalida = useInvalidaModerazione();
  return useMutation({
    mutationFn: async ({ id, ...dati }: DecisioneSegnalazioneInput & { id: string }) =>
      (await api.post<SegnalazioneAdmin>(`/admin/partenariati/segnalazioni/${id}/decidi`, dati))
        .data,
    onSettled: invalida,
  });
}

export function useDecidiRicorso() {
  const invalida = useInvalidaModerazione();
  return useMutation({
    mutationFn: async ({ id, ...dati }: DecisioneRicorsoInput & { id: string }) =>
      (
        await api.post<SegnalazioneAdmin>(
          `/admin/partenariati/segnalazioni/${id}/ricorso/decidi`,
          dati,
        )
      ).data,
    onSettled: invalida,
  });
}

/** Anteprima della motivazione formale (statement of reasons) che
 *  riceverebbe l'autore con questa decisione: nessuna scrittura, e il testo è
 *  quello che il server invierà (stesso modello). Solo con una motivazione
 *  valida (20..2000). */
export function useAnteprimaStatement(
  id: string,
  dati: DecisioneSegnalazioneInput | null,
) {
  return useQuery({
    queryKey: [...SEGNALAZIONI, id, "anteprima", dati],
    queryFn: async () =>
      (
        await api.post<AnteprimaStatement>(`/admin/partenariati/segnalazioni/${id}/anteprima`, dati)
      ).data,
    enabled: dati !== null,
    staleTime: Infinity,
    placeholderData: keepPreviousData,
    retry: false,
  });
}

/** Finestra di ±10 messaggi attorno al messaggio segnalato: si legge solo su
 *  azione esplicita dell'admin (`abilitato`), e una volta sola per apertura
 *  (ogni lettura è registrata: la chiave sta fuori dalle radici invalidate). */
export function useContestoSegnalazione(id: string, abilitato: boolean) {
  return useQuery({
    queryKey: contestoKey(id),
    queryFn: async () =>
      (
        await api.get<ContestoSegnalazione>(`/admin/partenariati/segnalazioni/${id}/contesto`, {
          params: { completo: false },
        })
      ).data,
    enabled: abilitato,
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

/** La conversazione intera: solo con una motivazione, che il server registra
 *  in audit. È una lettura, ma parte SOLO dal bottone (mai da un refetch):
 *  per questo è una mutation, in POST con la motivazione nel corpo (mai
 *  nell'URL, che finisce nei log di accesso). */
export function useContestoCompleto(id: string) {
  return useMutation({
    mutationFn: async (motivazione: string) =>
      (
        await api.post<ContestoSegnalazione>(`/admin/partenariati/segnalazioni/${id}/contesto`, {
          motivazione,
        })
      ).data,
  });
}

// ---- Sospensione e ripristino diretti -----------------------------------------

interface AzioneOggetto {
  oggetto: OggettoModerazione;
  id: string;
  motivazione: string;
}

/** Tipo dell'oggetto nel percorso (`/admin/partenariati/{oggetto_tipo}/{id}/…`):
 *  id della call, codice pubblico del profilo o id del messaggio, come nelle
 *  segnalazioni. */
const PERCORSO_OGGETTO: Record<OggettoModerazione, string> = {
  call: "call",
  profilo: "profilo",
  messaggio: "messaggio",
};

export function useSospendiOggetto() {
  const invalida = useInvalidaModerazione();
  return useMutation({
    mutationFn: async ({ oggetto, id, motivazione }: AzioneOggetto) =>
      (
        await api.post<EsitoSospensione>(
          `/admin/partenariati/${PERCORSO_OGGETTO[oggetto]}/${encodeURIComponent(id)}/sospendi`,
          { motivazione },
        )
      ).data,
    onSettled: invalida,
  });
}

export function useRipristinaOggetto() {
  const invalida = useInvalidaModerazione();
  return useMutation({
    mutationFn: async ({ oggetto, id, motivazione }: AzioneOggetto) =>
      (
        await api.post<EsitoSospensione>(
          `/admin/partenariati/${PERCORSO_OGGETTO[oggetto]}/${encodeURIComponent(id)}/ripristina`,
          { motivazione },
        )
      ).data,
    onSettled: invalida,
  });
}

// ---- Call ---------------------------------------------------------------------

export function useAdminCall(filtri: { stato: StatoCall | ""; q: string; page: number }) {
  const attivo = useAttivo();
  const params: Record<string, string | number> = { page: filtri.page, page_size: PAGE_SIZE };
  if (filtri.stato) params.stato = filtri.stato;
  if (filtri.q) params.q = filtri.q;
  return useQuery({
    queryKey: [...CALL, params],
    queryFn: async () =>
      (await api.get<Page<CallAdmin>>("/admin/partenariati/call", { params })).data,
    enabled: attivo,
    placeholderData: keepPreviousData,
    retry: senzaRetry4xx,
  });
}

// ---- Metriche e costi -----------------------------------------------------------

export interface Periodo {
  /** YYYY-MM-DD, estremi compresi (giorni Europe/Rome). */
  da: string;
  a: string;
}

export function useMetrichePartenariati(periodo: Periodo, abilitato: boolean) {
  const attivo = useAttivo();
  return useQuery({
    queryKey: [...ROOT, "metriche", periodo],
    queryFn: async () =>
      (await api.get<MetrichePartenariati>("/admin/partenariati/metriche", { params: periodo }))
        .data,
    enabled: attivo && abilitato,
    placeholderData: keepPreviousData,
    retry: senzaRetry4xx,
  });
}

export function useCostiPartenariati(periodo: Periodo, abilitato: boolean) {
  const attivo = useAttivo();
  return useQuery({
    queryKey: [...ROOT, "costi", periodo],
    queryFn: async () =>
      (await api.get<CostiPartenariati>("/admin/partenariati/costi", { params: periodo })).data,
    enabled: attivo && abilitato,
    placeholderData: keepPreviousData,
    retry: senzaRetry4xx,
  });
}

// ---- Identità verificata dall'admin -------------------------------------------

export function useAdminIdentita(stato: FiltroIdentitaAdmin, page: number) {
  const attivo = useAttivo();
  const params: Record<string, string | number> = { stato, page, page_size: PAGE_SIZE };
  return useQuery({
    queryKey: [...IDENTITA, params],
    queryFn: async () =>
      (await api.get<Page<IdentitaAdmin>>("/admin/partenariati/identita", { params })).data,
    enabled: attivo,
    placeholderData: keepPreviousData,
    retry: senzaRetry4xx,
  });
}

export function useDecidiIdentita() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ companyId, ...dati }: IdentitaDecisioneInput & { companyId: string }) =>
      (await api.post<IdentitaEsito>(`/admin/partenariati/identita/${companyId}/decidi`, dati))
        .data,
    onSettled: () => queryClient.invalidateQueries({ queryKey: IDENTITA }),
  });
}

export function useRevocaIdentita() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ companyId, ...dati }: IdentitaRevocaInput & { companyId: string }) =>
      (await api.post<IdentitaEsito>(`/admin/partenariati/identita/${companyId}/revoca`, dati))
        .data,
    onSettled: () => queryClient.invalidateQueries({ queryKey: IDENTITA }),
  });
}

// ---- Estrazioni delle regole (WP3) ----------------------------------------------

export function useAdminEstrazioni(
  filtri: { stato: EstrazioneAdmin["stato"] | ""; esito: "estratta" | "nessun_segnale" | "" },
  page: number,
) {
  const attivo = useAttivo();
  const params: Record<string, string | number> = { page, page_size: PAGE_SIZE };
  if (filtri.stato) params.stato = filtri.stato;
  if (filtri.esito) params.esito = filtri.esito;
  return useQuery({
    queryKey: [...ESTRAZIONI, params],
    queryFn: async () =>
      (await api.get<Page<EstrazioneAdmin>>("/admin/partenariati/estrazioni", { params })).data,
    enabled: attivo,
    placeholderData: keepPreviousData,
    retry: senzaRetry4xx,
    // Le estrazioni in corso si aggiornano da sole finché la scheda è aperta.
    refetchInterval: (query) =>
      query.state.data?.items.some((e) => e.stato === "in_corso") ? 10_000 : false,
  });
}

/** Nuova estrazione di un bando: paga sempre il modello (budget del giorno).
 *  202 se partita, 200 se era già in corso. */
export function useForzaEstrazione() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ bandoId, ignoraCooldown }: { bandoId: number; ignoraCooldown: boolean }) =>
      (
        await api.post<PartenariatoBando>(`/admin/partenariati/estrazioni/${bandoId}`, {
          ignora_cooldown: ignoraCooldown,
        })
      ).data,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ESTRAZIONI }),
  });
}

/** Run manuale dello scheduler di oggi (409 se già eseguita senza `ripeti`). */
export function useRunPartenariati() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (ripeti: boolean) =>
      (
        await api.post<PartenariatiRun>("/admin/partenariati/run", null, {
          params: ripeti ? { ripeti: true } : undefined,
          timeout: RUN_TIMEOUT_MS,
        })
      ).data,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ROOT }),
  });
}

/** Il 409 di una run già eseguita oggi (per proporre «Esegui di nuovo»). */
export const runGiaEseguita = (errore: unknown) => apiErrorCode(errore) === "conflict";
