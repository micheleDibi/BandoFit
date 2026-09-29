import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api, apiErrorCode } from "../lib/api";
import type {
  BudgetConsorzioInput,
  Consorzio,
  DocumentoStatoInput,
  EsternoInput,
  MembroAggiornaInput,
  MembroConfermaInput,
} from "../types";
import { useActiveCompany } from "./useActiveCompany";
import { useAuth } from "./useAuth";
import { callAnteprimaKey, callKey } from "./useCallPartenariato";
import { useFunzioni } from "./useFunzioni";

/* Consorzio della call (WP8): membri, verifica delle regole, copertura dei
 * requisiti, budget e documenti. Tutto deterministico lato server (nessun
 * modello): le risposte arrivano subito. La chiave sta sotto la radice della
 * call (`["partenariati", "call", id, …]`), così un'invalidazione della call
 * copre anche il consorzio; l'azienda attiva entra nella chiave perché la
 * risposta dipende da chi guarda (creatore o membro, dettagli privati). Le
 * scritture si attribuiscono all'azienda della RICHIESTA, fissata in onMutate. */

export const consorzioKey = (id: string | undefined, aziendaId: string | null) =>
  ["partenariati", "call", id, "consorzio", aziendaId] as const;

const LISTE_CALL_ROOT = ["partenariati", "call", "lista"] as const;
const PER_TE_ROOT = ["partenariati", "per-te"] as const;

/** Il 404 (modulo spento, call non tua né di una controparte) e il 409
 *  dell'azienda mancante non cambiano riprovando. */
const retry = (tentativi: number, errore: unknown) => {
  const codice = apiErrorCode(errore);
  return codice !== "not_found" && codice !== "azienda_mancante" && tentativi < 2;
};

/** La risposta di una scrittura è il consorzio aggiornato per chi guarda? */
function isConsorzio(dati: unknown): dati is Consorzio {
  return (
    !!dati &&
    typeof dati === "object" &&
    Array.isArray((dati as Consorzio).membri) &&
    typeof (dati as Consorzio).validazione === "object"
  );
}

export function useConsorzio(id: string | undefined, abilitato = true) {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  const { activeCompanyId } = useActiveCompany();
  return useQuery({
    queryKey: consorzioKey(id, activeCompanyId),
    queryFn: async () => (await api.get<Consorzio>(`/partenariati/call/${id}/consorzio`)).data,
    enabled: !!session && partenariatiAttivo && !!id && abilitato,
    staleTime: 15_000,
    retry,
  });
}

// ---- Scritture -----------------------------------------------------------------

interface Avvio {
  azienda: string | null;
}

/** Cosa rileggere oltre al consorzio: il dettaglio della call (budget, vista
 *  della controparte), le liste (fascia di budget nelle card) e «Per te»
 *  (chi esce dal consorzio non è più impegnato sul bando). */
interface Altro {
  call?: boolean;
  liste?: boolean;
  perTe?: boolean;
}

function rileggiAltro(queryClient: QueryClient, callId: string, azienda: string | null, altro: Altro) {
  if (altro.call) {
    queryClient.invalidateQueries({ queryKey: callKey(callId, azienda), exact: true });
    queryClient.invalidateQueries({ queryKey: callAnteprimaKey(callId, azienda) });
  }
  if (altro.liste) queryClient.invalidateQueries({ queryKey: LISTE_CALL_ROOT });
  if (altro.perTe) queryClient.invalidateQueries({ queryKey: PER_TE_ROOT });
}

/** Scrittura sul consorzio: se il server risponde con il consorzio aggiornato
 *  lo si scrive in cache, altrimenti lo si rilegge. Un rifiuto può voler dire
 *  che il consorzio è cambiato altrove (un membro è uscito, la call si è
 *  chiusa): si rilegge comunque. */
function useScritturaConsorzio<V>(
  callId: string | undefined,
  richiesta: (variabili: V) => Promise<unknown>,
  altro: Altro = {},
) {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: richiesta,
    onSuccess: (dati, _variabili, avvio) => {
      if (!callId) return;
      const azienda = avvio?.azienda ?? null;
      const chiave = consorzioKey(callId, azienda);
      if (isConsorzio(dati)) queryClient.setQueryData(chiave, dati);
      else queryClient.invalidateQueries({ queryKey: chiave, exact: true });
      rileggiAltro(queryClient, callId, azienda, altro);
    },
    onError: (_errore, _variabili, avvio) => {
      if (!callId) return;
      const azienda = avvio?.azienda ?? null;
      queryClient.invalidateQueries({ queryKey: consorzioKey(callId, azienda), exact: true });
      rileggiAltro(queryClient, callId, azienda, altro);
    },
  });
}

const base = (callId: string | undefined) => `/partenariati/call/${callId}/consorzio`;

/** Ruolo, posizione e quota di un membro (solo il creatore). Per un membro
 *  diverso dal creatore la modifica chiede una nuova conferma. */
export function useAggiornaMembro(callId: string | undefined) {
  return useScritturaConsorzio(
    callId,
    async ({ membroId, dati }: { membroId: string; dati: MembroAggiornaInput }) =>
      (await api.put<Consorzio>(`${base(callId)}/membri/${membroId}`, dati)).data,
  );
}

/** Conferma della partecipazione: l'azienda del membro, oppure il creatore
 *  per sé e per i membri esterni. Serve una quota (non a un partner
 *  associato). Si mandano ruolo, posizione e quota mostrati: se sono cambiati
 *  il server risponde 409 `membro_modificato` e il consorzio si rilegge. */
export function useConfermaMembro(callId: string | undefined) {
  return useScritturaConsorzio(
    callId,
    async ({ membroId, visti }: { membroId: string; visti: MembroConfermaInput }) =>
      (await api.post<Consorzio>(`${base(callId)}/membri/${membroId}/conferma`, visti)).data,
  );
}

/** Uscita dal consorzio: il membro esce da sé, il creatore rimuove un membro
 *  (la riga del creatore non si toglie). */
export function useEsciMembro(callId: string | undefined) {
  return useScritturaConsorzio(
    callId,
    async ({ membroId }: { membroId: string }) =>
      (await api.post<Consorzio>(`${base(callId)}/membri/${membroId}/esci`, null)).data,
    { call: true, liste: true, perTe: true },
  );
}

/** Aggiunge (senza `membroId`) o modifica un membro esterno (solo il
 *  creatore). Modificare un esterno uscito lo ripropone. */
export function useSalvaEsterno(callId: string | undefined) {
  return useScritturaConsorzio(
    callId,
    async ({ membroId, dati }: { membroId?: string | null; dati: EsternoInput }) =>
      (membroId
        ? await api.put<Consorzio>(`${base(callId)}/esterni/${membroId}`, dati)
        : await api.post<Consorzio>(`${base(callId)}/esterni`, dati)
      ).data,
  );
}

/** Fascia pubblica e budget esatto riservato (solo il creatore): cambiano
 *  anche il dettaglio della call e le card. */
export function useSalvaBudgetConsorzio(callId: string | undefined) {
  return useScritturaConsorzio(
    callId,
    async (dati: BudgetConsorzioInput) =>
      (await api.put<Consorzio>(`${base(callId)}/budget`, dati)).data,
    { call: true, liste: true },
  );
}

/** Stato e note di un documento della checklist (solo il creatore). */
export function useStatoDocumento(callId: string | undefined) {
  return useScritturaConsorzio(
    callId,
    async ({ codice, dati }: { codice: string; dati: DocumentoStatoInput }) =>
      (
        await api.put<Consorzio>(
          `${base(callId)}/documenti/${encodeURIComponent(codice)}`,
          dati,
        )
      ).data,
  );
}
