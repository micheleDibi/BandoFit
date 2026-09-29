import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api, apiErrorCode } from "../lib/api";
import type {
  Conversazione,
  ConversazioneCard,
  Messaggio,
  MessaggiPage,
  MessaggioInput,
  Page,
} from "../types";
import { useActiveCompany } from "./useActiveCompany";
import { useAuth } from "./useAuth";
import { useFunzioni } from "./useFunzioni";

/* Conversazioni tra le due aziende di una candidatura accettata (WP7).
 * Radice `["partenariati", "conversazioni" | "conversazione", …]` con
 * l'azienda attiva nella chiave. Polling: la lista ogni 60 s, i messaggi ogni
 * 10 s, SOLO con la scheda del browser visibile (TanStack sospende anche da
 * solo gli intervalli in background; qui lo si dice esplicitamente). I
 * messaggi nuovi si chiedono con il cursore `dopo` e si accumulano in cache. */

export const CONVERSAZIONI_PAGINA = 20;
/** Messaggi per richiesta (come il server). */
export const MESSAGGI_BLOCCO = 50;
const LISTA_POLLING_MS = 60_000;
const MESSAGGI_POLLING_MS = 10_000;
/** Con il cursore `dopo` si continua finché il server dice che ce ne sono
 *  altri, ma al massimo per questi giri (poi al prossimo polling). */
const GIRI_MAX = 5;

const CONVERSAZIONI_ROOT = ["partenariati", "conversazioni"] as const;
const RIEPILOGO_ROOT = ["partenariati", "riepilogo"] as const;

export const conversazioniKey = (aziendaId: string | null, page: number) =>
  ["partenariati", "conversazioni", aziendaId, page] as const;
export const conversazioneKey = (id: string | undefined, aziendaId: string | null) =>
  ["partenariati", "conversazione", id, aziendaId] as const;
export const messaggiKey = (id: string | undefined, aziendaId: string | null) =>
  ["partenariati", "conversazione", id, aziendaId, "messaggi"] as const;

/** I messaggi in cache: crescenti per id, senza doppioni. */
export interface StatoMessaggi {
  items: Messaggio[];
  /** Ci sono messaggi più vecchi da caricare («Messaggi precedenti»). */
  ha_precedenti: boolean;
}

const retry = (tentativi: number, errore: unknown) =>
  apiErrorCode(errore) !== "not_found" && tentativi < 2;

const soloVisibile = (ms: number) => () =>
  typeof document === "undefined" || document.visibilityState === "visible" ? ms : false;

/** Unisce due elenchi di messaggi: per id, in ordine crescente; a parità
 *  vince la versione di `nuovi`. Il polling rilegge solo i messaggi DOPO
 *  l'ultimo in cache: un messaggio già in cache e oscurato poi dalla
 *  moderazione si aggiorna solo con una lettura completa (la cache dei
 *  messaggi non sopravvive all'uscita dalla pagina, vedi `useMessaggi`). */
export function unisciMessaggi(prima: readonly Messaggio[], nuovi: readonly Messaggio[]): Messaggio[] {
  const perId = new Map<number, Messaggio>();
  for (const m of prima) perId.set(m.id, m);
  for (const m of nuovi) perId.set(m.id, m);
  return [...perId.values()].sort((a, b) => a.id - b.id);
}

const ultimoId = (items: readonly Messaggio[]) =>
  items.reduce((massimo, m) => (m.id > massimo ? m.id : massimo), 0);
const primoId = (items: readonly Messaggio[]) =>
  items.reduce((minimo, m) => (m.id < minimo ? m.id : minimo), Number.POSITIVE_INFINITY);

/** Chiave di idempotenza di un invio (UUID v4). */
export function nuovoClientMsgId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

// ---- Letture -------------------------------------------------------------------

/** Conversazioni dell'azienda attiva, con i non letti per l'utente. */
export function useConversazioni(page: number, abilitato = true) {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  const { activeCompanyId } = useActiveCompany();
  return useQuery({
    queryKey: conversazioniKey(activeCompanyId, page),
    queryFn: async () =>
      (
        await api.get<Page<ConversazioneCard>>("/partenariati/conversazioni", {
          params: { page, page_size: CONVERSAZIONI_PAGINA },
        })
      ).data,
    enabled: !!session && partenariatiAttivo && abilitato,
    staleTime: 30_000,
    refetchInterval: soloVisibile(LISTA_POLLING_MS),
    retry,
  });
}

/** Una conversazione (solo le due aziende; 404 per tutti gli altri). Si
 *  rilegge ogni minuto: chiusura o azienda non più attiva cambiano cosa si
 *  può fare. */
export function useConversazione(id: string | undefined) {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  const { activeCompanyId } = useActiveCompany();
  return useQuery({
    queryKey: conversazioneKey(id, activeCompanyId),
    queryFn: async () => (await api.get<Conversazione>(`/partenariati/conversazioni/${id}`)).data,
    enabled: !!session && partenariatiAttivo && !!id,
    staleTime: 15_000,
    refetchInterval: soloVisibile(LISTA_POLLING_MS),
    retry,
  });
}

async function leggiMessaggi(id: string, params: { dopo?: number; prima?: number }) {
  return (
    await api.get<MessaggiPage>(`/partenariati/conversazioni/${id}/messaggi`, {
      params: { ...params, limite: MESSAGGI_BLOCCO },
    })
  ).data;
}

/** Un giro di lettura dei messaggi (puro, per i test): senza messaggi in
 *  cache gli ultimi, altrimenti quelli dopo l'ultimo in cache, a blocchi
 *  finché il server dice che ce ne sono altri (al più `GIRI_MAX`). Il
 *  risultato si unisce alla cache letta DOPO le risposte (`inCache` si
 *  richiama), così un messaggio aggiunto nel frattempo non si perde. */
export async function aggiornaMessaggi(
  leggi: (params: { dopo?: number }) => Promise<MessaggiPage>,
  inCache: () => StatoMessaggi | undefined,
): Promise<StatoMessaggi> {
  const prima = inCache();
  if (!prima || prima.items.length === 0) {
    const pagina = await leggi({});
    return {
      items: unisciMessaggi(inCache()?.items ?? [], pagina.items),
      ha_precedenti: pagina.ha_altri,
    };
  }
  let dopo = ultimoId(prima.items);
  const nuovi: Messaggio[] = [];
  for (let giro = 0; giro < GIRI_MAX; giro++) {
    const pagina = await leggi({ dopo });
    nuovi.push(...pagina.items);
    const ultimo = ultimoId(pagina.items);
    if (!pagina.ha_altri || ultimo <= dopo) break;
    dopo = ultimo;
  }
  const attuali = inCache() ?? prima;
  return { items: unisciMessaggi(attuali.items, nuovi), ha_precedenti: attuali.ha_precedenti };
}

/** Messaggi della conversazione. La prima lettura prende gli ultimi; poi
 *  ogni 10 s (scheda visibile) solo quelli dopo l'ultimo in cache. Il
 *  risultato si unisce alla cache ATTUALE, riletta dopo la risposta: un
 *  messaggio appena inviato non sparisce se il polling era già partito. La
 *  cache si butta appena si esce dalla pagina (`gcTime: 0`): tornandoci si
 *  rileggono gli ultimi messaggi da capo, con gli oscuramenti nel frattempo. */
export function useMessaggi(id: string | undefined, abilitato = true) {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  const { activeCompanyId } = useActiveCompany();
  const queryClient = useQueryClient();
  const queryKey = messaggiKey(id, activeCompanyId);
  return useQuery({
    queryKey,
    queryFn: () =>
      aggiornaMessaggi(
        (params) => leggiMessaggi(id as string, params),
        () => queryClient.getQueryData<StatoMessaggi>(queryKey),
      ),
    enabled: !!session && partenariatiAttivo && !!id && abilitato,
    // Dati sempre «vecchi»: il polling e il ritorno sulla scheda rileggono.
    staleTime: 0,
    gcTime: 0,
    refetchInterval: soloVisibile(MESSAGGI_POLLING_MS),
    retry,
  });
}

// ---- Scritture -----------------------------------------------------------------

interface Avvio {
  azienda: string | null;
}

/** «Messaggi precedenti»: il blocco prima del più vecchio in cache. */
export function useCaricaPrecedenti(id: string | undefined) {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async () => {
      const chiave = messaggiKey(id, activeCompanyId);
      const inCache = queryClient.getQueryData<StatoMessaggi>(chiave);
      const primo = primoId(inCache?.items ?? []);
      return leggiMessaggi(id as string, Number.isFinite(primo) ? { prima: primo } : {});
    },
    onSuccess: (pagina, _variabili, avvio) => {
      queryClient.setQueryData<StatoMessaggi>(messaggiKey(id, avvio?.azienda ?? null), (prima) => ({
        items: unisciMessaggi(prima?.items ?? [], pagina.items),
        ha_precedenti: pagina.ha_altri,
      }));
    },
  });
}

function rileggiConversazione(queryClient: QueryClient, id: string | undefined, avvio: Avvio | undefined) {
  queryClient.invalidateQueries({ queryKey: conversazioneKey(id, avvio?.azienda ?? null), exact: true });
}

/** Invio di un messaggio (solo il titolare). `client_msg_id` lo sceglie chi
 *  chiama e lo riusa se ritenta lo stesso testo: il server non duplica. */
export function useInviaMessaggio(id: string | undefined) {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async (dati: MessaggioInput) =>
      (await api.post<Messaggio>(`/partenariati/conversazioni/${id}/messaggi`, dati)).data,
    onSuccess: (messaggio, _variabili, avvio) => {
      queryClient.setQueryData<StatoMessaggi>(messaggiKey(id, avvio?.azienda ?? null), (prima) => ({
        items: unisciMessaggi(prima?.items ?? [], [messaggio]),
        ha_precedenti: prima?.ha_precedenti ?? false,
      }));
      queryClient.invalidateQueries({ queryKey: CONVERSAZIONI_ROOT });
    },
    onError: (errore, _variabili, avvio) => {
      // Conversazione chiusa o azienda non più attiva nel frattempo (il
      // limite anti-abuso dei messaggi non cambia nulla).
      if (apiErrorCode(errore) !== "limite_messaggi") rileggiConversazione(queryClient, id, avvio);
    },
  });
}

/** Segna come letti i messaggi fino a `fino_a_id` (per l'utente). */
export function useSegnaLetto(id: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (finoA: number) =>
      api.post(`/partenariati/conversazioni/${id}/letto`, { fino_a_id: finoA }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: RIEPILOGO_ROOT });
      queryClient.invalidateQueries({ queryKey: CONVERSAZIONI_ROOT });
    },
  });
}

/** Chiusura (solo il titolare dell'azienda che ha creato la call): lo
 *  storico resta in sola lettura. */
export function useChiudiConversazione(id: string | undefined) {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async () => {
      await api.post(`/partenariati/conversazioni/${id}/chiudi`);
    },
    // Si rilegge il dettaglio (con i permessi calcolati dal server) anche dopo
    // un rifiuto: la conversazione può essere cambiata altrove.
    onSettled: (_dati, _errore, _variabili, avvio) => {
      rileggiConversazione(queryClient, id, avvio);
      queryClient.invalidateQueries({ queryKey: CONVERSAZIONI_ROOT });
    },
  });
}
