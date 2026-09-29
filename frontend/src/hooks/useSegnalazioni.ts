import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiErrorCode } from "../lib/api";
import type { RicorsoInput, SegnalazioneEsito } from "../types";
import { useActiveCompany } from "./useActiveCompany";
import { useAuth } from "./useAuth";
import { useFunzioni } from "./useFunzioni";

/** Segnalazioni dei partenariati viste da chi ha segnalato o dall'azienda
 *  autrice del contenuto (WP9). L'autore si riconosce dall'azienda attiva:
 *  la chiave la contiene, e la risposta di un ricorso si scrive sotto
 *  l'azienda della richiesta. */
export const segnalazioneKey = (id: string | undefined, aziendaId: string | null) =>
  ["partenariati", "segnalazione", id, aziendaId] as const;

export function useSegnalazione(id: string | undefined) {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  const { activeCompanyId } = useActiveCompany();
  return useQuery({
    queryKey: segnalazioneKey(id, activeCompanyId),
    queryFn: async () =>
      (await api.get<SegnalazioneEsito>(`/partenariati/segnalazioni/${id}`)).data,
    enabled: !!session && partenariatiAttivo && !!id,
    staleTime: 30_000,
    // Chi non è né segnalante né autore riceve 404: riprovare non cambia nulla.
    retry: (tentativi, errore) => apiErrorCode(errore) !== "not_found" && tentativi < 2,
  });
}

/** Ricorso interno (uno solo, entro 6 mesi): testo 20..2000 caratteri. */
export function useRicorsoSegnalazione(id: string | undefined) {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: () => ({ azienda: activeCompanyId }),
    mutationFn: async (dati: RicorsoInput) =>
      (await api.post<SegnalazioneEsito>(`/partenariati/segnalazioni/${id}/ricorso`, dati)).data,
    onSuccess: (dati, _variabili, avvio) => {
      queryClient.setQueryData(segnalazioneKey(id, avvio?.azienda ?? null), dati);
      void queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
    onError: (_errore, _variabili, avvio) => {
      // Ricorso già presentato o termine scaduto: si rilegge lo stato.
      void queryClient.invalidateQueries({
        queryKey: segnalazioneKey(id, avvio?.azienda ?? activeCompanyId),
      });
    },
  });
}
