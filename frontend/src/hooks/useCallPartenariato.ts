import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api, apiErrorCode } from "../lib/api";
import type {
  AnteprimaCall,
  CallAggiornaInput,
  CallCard,
  CallCreaInput,
  CallPubblica,
  CallVistaCreatore,
  ChiudiCallInput,
  GapCall,
  JobAiCall,
  Page,
  PosizioneInput,
  RegoleConfermaInput,
  RequisitoInput,
  SegnalazioneInput,
  SegnalazioneRicevuta,
  VersioneCall,
} from "../types";
import { useActiveCompany } from "./useActiveCompany";
import { useAuth } from "./useAuth";
import { useFunzioni } from "./useFunzioni";
import { partenariatoBandoKey } from "./usePartenariatoBando";

/** Polling dei job AI della call (posizioni, testi): ogni 3 s finché uno è
 *  in corso e partito da meno della finestra. Il server chiude da solo, alla
 *  lettura successiva, un job rimasto appeso oltre i 10 minuti. */
const JOB_POLLING_MS = 3_000;
export const JOB_FINESTRA_POLLING_MS = 12 * 60_000;

/** Le POST dei job rispondono subito (202, il lavoro è in background): il
 *  timeout evita solo un'attesa senza fine se la rete cade. */
const AVVIO_JOB_TIMEOUT_MS = 30_000;
/** La gap analysis è deterministica (nessun LLM) ma legge più fonti. */
const GENERA_REQUISITI_TIMEOUT_MS = 30_000;

/** Radice unica `["partenariati", "call", …]`: un solo
 *  `invalidateQueries({queryKey: ["partenariati", "call", id]})` copre
 *  dettaglio, anteprima e versioni (e dal WP6 suggeriti, candidature…). Le
 *  risposte dipendono dall'azienda attiva (chi guarda), che entra nella
 *  chiave; le scritture vanno sotto l'azienda della RICHIESTA (fissata in
 *  onMutate), come il profilo partner. */
export const CALL_ROOT = ["partenariati", "call"] as const;
const LISTE_ROOT = ["partenariati", "call", "lista"] as const;
export const callListaKey = (aziendaId: string | null, vista: string) =>
  ["partenariati", "call", "lista", aziendaId, vista] as const;
export const callKey = (id: string | undefined, aziendaId: string | null) =>
  ["partenariati", "call", id, aziendaId] as const;
export const callAnteprimaKey = (id: string | undefined, aziendaId: string | null) =>
  ["partenariati", "call", id, aziendaId, "anteprima"] as const;
export const callVersioniKey = (id: string | undefined, aziendaId: string | null) =>
  ["partenariati", "call", id, aziendaId, "versioni"] as const;

/** Il dettaglio può essere la vista del creatore o (dal WP6, per le altre
 *  aziende) la proiezione pubblica: la distingue `editable`, che esiste solo
 *  nella prima. */
export type CallDettaglio = CallVistaCreatore | CallPubblica;

export function isVistaCreatore(dati: CallDettaglio | undefined): dati is CallVistaCreatore {
  return !!dati && typeof (dati as CallVistaCreatore).editable === "boolean";
}

/** Un job AI sta lavorando ed è partito da meno della finestra di polling. */
export function jobInCorsoRecente(job: JobAiCall<unknown> | undefined, ora = Date.now()) {
  if (!job || job.stato !== "in_corso" || !job.avviata_at) return false;
  const avvio = new Date(job.avviata_at).getTime();
  return Number.isFinite(avvio) && ora - avvio < JOB_FINESTRA_POLLING_MS;
}

function qualcheJobInCorso(dati: CallDettaglio | undefined): boolean {
  if (!isVistaCreatore(dati)) return false;
  return jobInCorsoRecente(dati.ai_posizioni) || jobInCorsoRecente(dati.ai_testi);
}

/** Il 404 (modulo spento, call di un'altra azienda) non cambia riprovando. */
const retry = (tentativi: number, errore: unknown) =>
  apiErrorCode(errore) !== "not_found" && tentativi < 2;

interface Avvio {
  azienda: string | null;
}

/** Dopo una scrittura riuscita sulla call: dettaglio aggiornato con la
 *  risposta, liste, anteprima e versioni da rileggere. */
function scriviCall(queryClient: QueryClient, avvio: Avvio | undefined, dati: CallVistaCreatore) {
  const azienda = avvio?.azienda ?? null;
  queryClient.setQueryData(callKey(dati.id, azienda), dati);
  queryClient.invalidateQueries({ queryKey: callAnteprimaKey(dati.id, azienda) });
  queryClient.invalidateQueries({ queryKey: callVersioniKey(dati.id, azienda) });
  queryClient.invalidateQueries({ queryKey: LISTE_ROOT });
}

/** Un rifiuto può voler dire che la call è cambiata altrove (stato, limiti):
 *  si rilegge il dettaglio. */
function rileggiCall(queryClient: QueryClient, id: string | undefined, avvio: Avvio | undefined) {
  if (!id) return;
  queryClient.invalidateQueries({ queryKey: callKey(id, avvio?.azienda ?? null), exact: true });
}

// ---- Letture -------------------------------------------------------------------

/** Quante call della propria azienda si leggono in una volta (massimo del
 *  server): bastano, con al più 5 bozze e poche call attive per piano. */
export const MIE_CALL_PAGINA = 50;

/** Le call dell'azienda attiva (`?vista=mie`), dalla più recente. Anche i
 *  membri le leggono. La risposta è una pagina: qui si usa la prima. */
export function useMieCall() {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  const { activeCompanyId } = useActiveCompany();
  return useQuery({
    queryKey: callListaKey(activeCompanyId, "mie"),
    queryFn: async () =>
      (
        await api.get<Page<CallCard>>("/partenariati/call", {
          params: { vista: "mie", page: 1, page_size: MIE_CALL_PAGINA },
        })
      ).data,
    enabled: !!session && partenariatiAttivo,
    staleTime: 30_000,
    retry,
  });
}

/** Dettaglio della call. Polling solo mentre un job AI lavora. */
export function useCall(id: string | undefined) {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  const { activeCompanyId } = useActiveCompany();
  return useQuery({
    queryKey: callKey(id, activeCompanyId),
    queryFn: async () => (await api.get<CallDettaglio>(`/partenariati/call/${id}`)).data,
    enabled: !!session && partenariatiAttivo && !!id,
    staleTime: 15_000,
    retry,
    refetchInterval: (query) => (qualcheJobInCorso(query.state.data) ? JOB_POLLING_MS : false),
  });
}

/** «Come ti vedono»: la proiezione pubblica dei dati SALVATI + i rilievi. */
export function useAnteprimaCall(id: string | undefined, abilitata = true) {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  const { activeCompanyId } = useActiveCompany();
  return useQuery({
    queryKey: callAnteprimaKey(id, activeCompanyId),
    queryFn: async () =>
      (await api.get<AnteprimaCall>(`/partenariati/call/${id}/anteprima`)).data,
    enabled: !!session && partenariatiAttivo && !!id && abilitata,
    staleTime: 15_000,
    retry,
  });
}

/** Versioni pubblicate (solo per il creatore). */
export function useVersioniCall(id: string | undefined, abilitata = true) {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  const { activeCompanyId } = useActiveCompany();
  return useQuery({
    queryKey: callVersioniKey(id, activeCompanyId),
    queryFn: async () =>
      (await api.get<VersioneCall[]>(`/partenariati/call/${id}/versioni`)).data,
    enabled: !!session && partenariatiAttivo && !!id && abilitata,
    staleTime: 60_000,
    retry,
  });
}

// ---- Scritture (solo il titolare) ----------------------------------------------

/** Crea la bozza dal bando (201 con la vista del creatore). */
export function useCreaCall() {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async (dati: CallCreaInput) =>
      (await api.post<CallVistaCreatore>("/partenariati/call", dati)).data,
    onSuccess: (dati, _variabili, avvio) => scriviCall(queryClient, avvio, dati),
    // `call_gia_presente`: la call esistente va mostrata nelle liste.
    onError: () => queryClient.invalidateQueries({ queryKey: LISTE_ROOT }),
  });
}

/** Aggiornamento parziale (solo i campi passati). */
export function useAggiornaCall(id: string | undefined) {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async (dati: CallAggiornaInput) =>
      (await api.patch<CallVistaCreatore>(`/partenariati/call/${id}`, dati)).data,
    onSuccess: (dati, _variabili, avvio) => scriviCall(queryClient, avvio, dati),
    onError: (_errore, _variabili, avvio) => rileggiCall(queryClient, id, avvio),
  });
}

/** Conferma (o correzione) delle regole del bando: solo in bozza. */
export function useConfermaRegole(id: string | undefined) {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async (dati: RegoleConfermaInput) =>
      (await api.post<CallVistaCreatore>(`/partenariati/call/${id}/regole`, dati)).data,
    onSuccess: (dati, _variabili, avvio) => scriviCall(queryClient, avvio, dati),
    onError: (_errore, _variabili, avvio) => rileggiCall(queryClient, id, avvio),
  });
}

/** Gap analysis: PROPOSTE di requisiti con la copertura del creatore (non
 *  salvate). I requisiti già salvati conservano id, etichetta e «cercato». */
export function useGeneraRequisiti(id: string | undefined) {
  return useMutation({
    mutationFn: async () =>
      (
        await api.post<GapCall>(`/partenariati/call/${id}/requisiti/genera`, null, {
          timeout: GENERA_REQUISITI_TIMEOUT_MS,
        })
      ).data,
  });
}

/** Salva i requisiti (replace-all). Risponde con la sola gap analysis: il
 *  dettaglio si aggiorna con quella e si rilegge (motivi di blocco). */
export function useSalvaRequisiti(id: string | undefined) {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async (requisiti: RequisitoInput[]) =>
      (await api.put<GapCall>(`/partenariati/call/${id}/requisiti`, { requisiti })).data,
    onSuccess: (gap, _variabili, avvio) => {
      const azienda = avvio?.azienda ?? null;
      const chiave = callKey(id, azienda);
      queryClient.setQueryData<CallDettaglio>(chiave, (prima) =>
        isVistaCreatore(prima) ? { ...prima, gap } : prima,
      );
      queryClient.invalidateQueries({ queryKey: chiave, exact: true });
      queryClient.invalidateQueries({ queryKey: callAnteprimaKey(id, azienda) });
      queryClient.invalidateQueries({ queryKey: LISTE_ROOT });
    },
    onError: (_errore, _variabili, avvio) => rileggiCall(queryClient, id, avvio),
  });
}

export function useSalvaPosizioni(id: string | undefined) {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async (posizioni: PosizioneInput[]) =>
      (await api.put<CallVistaCreatore>(`/partenariati/call/${id}/posizioni`, { posizioni }))
        .data,
    onSuccess: (dati, _variabili, avvio) => scriviCall(queryClient, avvio, dati),
    onError: (_errore, _variabili, avvio) => rileggiCall(queryClient, id, avvio),
  });
}

/** Avvio di un job AI (posizioni o testi): 202 con la vista della call (job
 *  `in_corso`) oppure con il solo stato del job. Il polling del dettaglio fa
 *  il resto. La proposta non si salva mai da sola. */
function useAvviaJob(id: string | undefined, percorso: "posizioni" | "testi") {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  const campo = percorso === "posizioni" ? "ai_posizioni" : "ai_testi";
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async () =>
      (
        await api.post<CallVistaCreatore | JobAiCall<unknown>>(
          `/partenariati/call/${id}/${percorso}/proposta`,
          null,
          { timeout: AVVIO_JOB_TIMEOUT_MS },
        )
      ).data,
    onSuccess: (dati, _variabili, avvio) => {
      const chiave = callKey(id, avvio?.azienda ?? null);
      if ("editable" in dati) {
        queryClient.setQueryData(chiave, dati);
        return;
      }
      queryClient.setQueryData<CallDettaglio>(chiave, (prima) =>
        isVistaCreatore(prima) ? { ...prima, [campo]: dati } : prima,
      );
      queryClient.invalidateQueries({ queryKey: chiave, exact: true });
    },
    // Un rifiuto (job già in corso, limite di oggi) può voler dire che lo
    // stato è cambiato: si rilegge.
    onError: (_errore, _variabili, avvio) => rileggiCall(queryClient, id, avvio),
  });
}

export function useProponiPosizioni(id: string | undefined) {
  return useAvviaJob(id, "posizioni");
}

export function useProponiTesti(id: string | undefined) {
  return useAvviaJob(id, "testi");
}

/** Pubblicazione: consuma un posto tra le call attive del piano, cambia il
 *  conteggio delle call aperte del bando. Senza scadenza vale quella salvata
 *  o il default del server (min(scadenza del bando, oggi + 60 giorni)). */
export function usePubblicaCall(id: string | undefined) {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async (dati: { scadenza_call?: string | null }) =>
      (await api.post<CallVistaCreatore>(`/partenariati/call/${id}/pubblica`, dati)).data,
    onSuccess: (dati, _variabili, avvio) => {
      scriviCall(queryClient, avvio, dati);
      queryClient.invalidateQueries({ queryKey: ["entitlements"] });
      queryClient.invalidateQueries({ queryKey: partenariatoBandoKey(dati.bando.slug) });
    },
    onError: (_errore, _variabili, avvio) => {
      rileggiCall(queryClient, id, avvio);
      // Limiti del piano o stato del bando possono essere cambiati.
      queryClient.invalidateQueries({ queryKey: ["entitlements"] });
    },
  });
}

/** Chiusura manuale (completata o annullata): libera il posto della call. */
export function useChiudiCall(id: string | undefined) {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async (dati: ChiudiCallInput) =>
      (await api.post<CallVistaCreatore>(`/partenariati/call/${id}/chiudi`, dati)).data,
    onSuccess: (dati, _variabili, avvio) => {
      scriviCall(queryClient, avvio, dati);
      queryClient.invalidateQueries({ queryKey: ["entitlements"] });
      queryClient.invalidateQueries({ queryKey: partenariatoBandoKey(dati.bando.slug) });
    },
    onError: (_errore, _variabili, avvio) => rileggiCall(queryClient, id, avvio),
  });
}

/** Segnalazione di un contenuto (DSA): la conferma di ricezione arriva anche
 *  tra le notifiche. */
export function useSegnala() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (dati: SegnalazioneInput) =>
      (await api.post<SegnalazioneRicevuta>("/partenariati/segnalazioni", dati)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });
}
