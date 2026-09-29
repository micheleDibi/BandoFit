import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api, apiErrorCode } from "../lib/api";
import type {
  Candidatura,
  CandidaturaInput,
  FiltriCandidature,
  InvitoInput,
  Page,
  RifiutoInput,
} from "../types";
import { useActiveCompany } from "./useActiveCompany";
import { useAuth } from "./useAuth";
import { callKey } from "./useCallPartenariato";
import { useFunzioni } from "./useFunzioni";

/* Candidature spontanee e inviti (WP7): una sola entità con `tipo`. Radice
 * unica `["partenariati", "candidature", …]` con l'azienda attiva nella
 * chiave (la risposta dipende da chi guarda); le scritture si attribuiscono
 * all'azienda della RICHIESTA, fissata in onMutate. Solo il titolare scrive:
 * il server risponde 403 agli altri. */

/** Righe per pagina delle liste. */
export const CANDIDATURE_PAGINA = 20;

const CANDIDATURE_ROOT = ["partenariati", "candidature"] as const;
const RIEPILOGO_ROOT = ["partenariati", "riepilogo"] as const;
const CONVERSAZIONI_ROOT = ["partenariati", "conversazioni"] as const;
const LISTE_CALL_ROOT = ["partenariati", "call", "lista"] as const;
const PER_TE_ROOT = ["partenariati", "per-te"] as const;

export const candidatureKey = (
  aziendaId: string | null,
  filtri: FiltriCandidature,
  page: number,
) =>
  [
    "partenariati",
    "candidature",
    aziendaId,
    { direzione: filtri.direzione, stato: filtri.stato, call_id: filtri.call_id ?? null },
    page,
  ] as const;

/** Il 404 (modulo spento, call o candidatura non visibile) e il 409
 *  dell'azienda mancante non cambiano riprovando. */
const retry = (tentativi: number, errore: unknown) => {
  const codice = apiErrorCode(errore);
  return codice !== "not_found" && codice !== "azienda_mancante" && tentativi < 2;
};

/** Mentre arriva un'altra pagina resta la precedente, ma solo della STESSA
 *  lista (stessa azienda e stessi filtri): mai dati di un'altra azienda. */
function stessaListaAltraPagina(chiave: readonly unknown[]) {
  const base = JSON.stringify(chiave.slice(0, -1));
  return <T>(precedenti: T | undefined, query: { queryKey: readonly unknown[] } | undefined) =>
    query && JSON.stringify(query.queryKey.slice(0, -1)) === base ? precedenti : undefined;
}

/** Parametri della richiesta: solo i filtri valorizzati. */
function parametri(filtri: FiltriCandidature, page: number) {
  const params: Record<string, string | number> = {
    direzione: filtri.direzione,
    page,
    page_size: CANDIDATURE_PAGINA,
  };
  if (filtri.stato) params.stato = filtri.stato;
  if (filtri.call_id) params.call_id = filtri.call_id;
  return params;
}

/** Candidature e inviti dell'azienda attiva: `inviate` (le sue candidature e
 *  gli inviti che ha mandato) o `ricevute` (candidature sulle sue call e
 *  inviti ricevuti). Anche i membri le leggono. Con `call_id` solo quelle di
 *  una call: il parametro va al server e il client filtra comunque (una riga
 *  di un'altra call non compare mai nella scheda sbagliata). */
export function useCandidature(filtri: FiltriCandidature, page: number, abilitato = true) {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  const { activeCompanyId } = useActiveCompany();
  const queryKey = candidatureKey(activeCompanyId, filtri, page);
  return useQuery({
    queryKey,
    queryFn: async () => {
      const dati = (
        await api.get<Page<Candidatura>>("/partenariati/candidature", {
          params: parametri(filtri, page),
        })
      ).data;
      if (!filtri.call_id) return dati;
      const items = dati.items.filter((c) => c.call.id === filtri.call_id);
      if (items.length === dati.items.length) return dati;
      // Il server non ha filtrato per call: si mostra solo ciò che è di
      // questa call, senza totali né pagine che non le appartengono.
      return { ...dati, items, total: items.length, page: 1, total_pages: 1 };
    },
    enabled: !!session && partenariatiAttivo && abilitato,
    staleTime: 30_000,
    placeholderData: stessaListaAltraPagina(queryKey),
    retry,
  });
}

/** Una candidatura o un invito (`GET /partenariati/candidature/{id}`, solo
 *  le due aziende). Per chi ha creato la call porta il profilo pubblico
 *  dell'azienda candidata o invitata, finché è visibile (la lista non lo
 *  porta). Si legge su richiesta (`abilitato`). */
export const candidaturaKey = (id: string | undefined, aziendaId: string | null) =>
  ["partenariati", "candidature", aziendaId, "dettaglio", id] as const;

export function useCandidatura(id: string | undefined, abilitato = true) {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  const { activeCompanyId } = useActiveCompany();
  return useQuery({
    queryKey: candidaturaKey(id, activeCompanyId),
    queryFn: async () => (await api.get<Candidatura>(`/partenariati/candidature/${id}`)).data,
    enabled: !!session && partenariatiAttivo && !!id && abilitato,
    staleTime: 60_000,
    retry,
  });
}

// ---- Scritture -----------------------------------------------------------------

interface Avvio {
  azienda: string | null;
}

/** Dopo una scrittura (riuscita o no: un rifiuto può voler dire che lo stato
 *  è cambiato altrove) si rileggono liste, riepilogo e la call coinvolta. */
function rileggiDopo(queryClient: QueryClient, callId: string | undefined, avvio: Avvio | undefined) {
  queryClient.invalidateQueries({ queryKey: CANDIDATURE_ROOT });
  queryClient.invalidateQueries({ queryKey: RIEPILOGO_ROOT });
  if (callId) {
    queryClient.invalidateQueries({ queryKey: callKey(callId, avvio?.azienda ?? null), exact: true });
  }
}

/** Candidatura spontanea su una call pubblica (titolare; serve il profilo
 *  partner visibile e un piano che la includa). Consuma una candidatura del
 *  mese: si rileggono anche le quote. */
export function useInviaCandidatura(callId: string | undefined) {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async (dati: CandidaturaInput) =>
      (await api.post<Candidatura>(`/partenariati/call/${callId}/candidature`, dati)).data,
    onSettled: (_dati, _errore, _variabili, avvio) => {
      rileggiDopo(queryClient, callId, avvio);
      queryClient.invalidateQueries({ queryKey: ["entitlements"] });
      queryClient.invalidateQueries({ queryKey: LISTE_CALL_ROOT });
      queryClient.invalidateQueries({ queryKey: PER_TE_ROOT });
    },
  });
}

/** Invito a un'azienda suggerita, indicata con lo pseudonimo della call (mai
 *  il suo id). Solo il titolare dell'azienda che ha creato la call. */
export function useInvita(callId: string | undefined) {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async (dati: InvitoInput) =>
      (await api.post<Candidatura>(`/partenariati/call/${callId}/inviti`, dati)).data,
    onSettled: (_dati, _errore, _variabili, avvio) => {
      rileggiDopo(queryClient, callId, avvio);
      // I suggeriti portano lo stato del contatto.
      if (callId) {
        queryClient.invalidateQueries({
          queryKey: [...callKey(callId, avvio?.azienda ?? null), "suggeriti"],
        });
      }
    },
  });
}

export type Decisione = "accetta" | "rifiuta" | "ritira";

interface DecisioneVariabili {
  /** Id della candidatura o dell'invito. */
  id: string;
  /** La call (per rileggerne il dettaglio). */
  callId: string;
  decisione: Decisione;
  /** Solo per il rifiuto (facoltativo, fino a 500 caratteri). */
  motivo?: string | null;
}

/** Accetta o rifiuta (chi riceve: il creatore per le candidature, l'azienda
 *  invitata per gli inviti) o ritira (chi l'ha mandata). L'accettazione apre
 *  la conversazione: si rileggono anche le conversazioni. */
export function useDecidiCandidatura() {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async ({ id, decisione, motivo }: DecisioneVariabili) => {
      const corpo: RifiutoInput | undefined =
        decisione === "rifiuta" ? { motivo: motivo?.trim() || null } : undefined;
      return (
        await api.post<Candidatura>(`/partenariati/candidature/${id}/${decisione}`, corpo ?? null)
      ).data;
    },
    onSettled: (_dati, _errore, { callId, decisione }, avvio) => {
      rileggiDopo(queryClient, callId, avvio);
      if (decisione === "accetta") {
        queryClient.invalidateQueries({ queryKey: CONVERSAZIONI_ROOT });
      }
    },
  });
}
