import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { PartenariatoBando } from "../types";
import { useAuth } from "./useAuth";
import { useFunzioni } from "./useFunzioni";

/** L'analisi dura di solito 1-3 minuti. Il server chiude comunque come
 *  interrotta, alla lettura successiva, quella rimasta appesa oltre i 15
 *  minuti del claim: il polling non resta acceso per sempre. */
export const PARTENARIATO_FINESTRA_POLLING_MS = 15 * 60_000;
const POLLING_MS = 5_000;

/** Radice unica `["partenariati", …]` per tutte le query del modulo. Le regole
 *  sono del bando, non dell'azienda: la chiave non contiene l'azienda attiva
 *  (al cambio azienda la cache si svuota comunque). */
export const partenariatoBandoKey = (slug: string | undefined) =>
  ["partenariati", "bando", slug] as const;

/** C'è un'analisi che lavora: la prima (`in_corso`) o un aggiornamento mentre
 *  si servono le regole precedenti. */
export function analisiInCorso(dati: PartenariatoBando): boolean {
  return dati.stato === "in_corso" || dati.aggiornamento_in_corso;
}

/** L'analisi è partita da meno della finestra di polling. */
export function avviataDiRecente(dati: PartenariatoBando, ora = Date.now()): boolean {
  if (!dati.avviata_at) return false;
  const avvio = new Date(dati.avviata_at).getTime();
  return Number.isFinite(avvio) && ora - avvio < PARTENARIATO_FINESTRA_POLLING_MS;
}

/** Stato e regole di partenariato di un bando. Leggerle è gratuito: l'analisi
 *  (a carico della piattaforma) parte solo con `useAvviaAnalisiPartenariato`.
 *  Polling ogni 5 s mentre un'analisi lavora, entro la finestra. */
export function usePartenariatoBando(slug: string | undefined) {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  return useQuery({
    queryKey: partenariatoBandoKey(slug),
    queryFn: async () =>
      (await api.get<PartenariatoBando>(`/bandi/${slug}/partenariato`)).data,
    // A flag spento le rotte rispondono 404: non si chiamano proprio.
    enabled: !!session && !!slug && partenariatiAttivo,
    staleTime: 30_000,
    refetchInterval: (query) => {
      const dati = query.state.data;
      return dati && analisiInCorso(dati) && avviataDiRecente(dati) ? POLLING_MS : false;
    },
  });
}

/** Avvia (o rilancia) l'analisi delle regole del bando: 202 con lo stato in
 *  corso, 200 se le regole sono già fresche o un'analisi è già partita.
 *  `forza` serve solo per «Analizza comunque» dopo `nessun_segnale`. */
export function useAvviaAnalisiPartenariato(slug: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    // Il bando della RICHIESTA, fissato all'avvio: la risposta va scritta
    // sotto la sua chiave anche se nel frattempo la pagina è cambiata.
    onMutate: () => ({ slug }),
    mutationFn: async ({ forza }: { forza: boolean }) =>
      (
        await api.post<PartenariatoBando>(`/bandi/${slug}/partenariato/analisi`, { forza })
      ).data,
    onSuccess: (dati, _variabili, avvio) => {
      queryClient.setQueryData(partenariatoBandoKey(avvio?.slug ?? dati.bando_slug), dati);
    },
    // Un rifiuto (limite, budget, attesa) non cambia le regole ma può cambiare
    // `puo_avviare` e `riprova_dopo`: si rilegge lo stato.
    onError: (_errore, _variabili, avvio) => {
      queryClient.invalidateQueries({ queryKey: partenariatoBandoKey(avvio?.slug ?? slug) });
    },
  });
}
