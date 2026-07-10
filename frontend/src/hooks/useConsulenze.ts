import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useAuth } from "./useAuth";
import type { Consulenza, Slot } from "../types";

/** Richieste di consulto dell'Azienda (visibili anche agli account collegati,
 *  in sola lettura: le azioni sono del titolare). */
export function useConsulenze() {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["consulenze"],
    queryFn: async () => (await api.get<Consulenza[]>("/me/consulenze")).data,
    enabled: !!session,
  });
}

export function useConsulenza(requestId: string | undefined) {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["consulenze", requestId],
    queryFn: async () =>
      (await api.get<Consulenza>(`/me/consulenze/${requestId}`)).data,
    enabled: !!session && !!requestId,
  });
}

/** Slot liberi del progettista assegnato o di quello della proposta indicata
 *  (per prenotare contestualmente all'accettazione). */
export function useSlotDisponibili(
  requestId: string | undefined,
  propostaId: string | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ["consulenze", requestId, "slots", propostaId],
    queryFn: async () =>
      (
        await api.get<Slot[]>(`/me/consulenze/${requestId}/slots`, {
          params: propostaId ? { proposta: propostaId } : undefined,
        })
      ).data,
    enabled: enabled && !!requestId,
  });
}

/** Le azioni ritornano il dettaglio aggiornato: si scrive in cache e si
 *  invalida la lista (stesso pattern di useMe). */
function useConsulenzaMutation<TVariables>(
  mutationFn: (variables: TVariables) => Promise<Consulenza>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: (data) => {
      queryClient.setQueryData(["consulenze", data.id], data);
      queryClient.invalidateQueries({ queryKey: ["consulenze"], exact: true });
    },
  });
}

/** Attivazione dell'addon «Consulto esperto» su un AI-check completato. */
export function useCreateConsulenza() {
  return useConsulenzaMutation(async (aiCheckId: string) =>
    (await api.post<Consulenza>("/me/consulenze", { ai_check_id: aiCheckId })).data,
  );
}

export function useAccettaProposta(requestId: string) {
  return useConsulenzaMutation(
    async ({ propostaId, slotId }: { propostaId: string; slotId: string | null }) =>
      (
        await api.post<Consulenza>(
          `/me/consulenze/${requestId}/proposte/${propostaId}/accetta`,
          { slot_id: slotId },
        )
      ).data,
  );
}

export function useRifiutaProposta(requestId: string) {
  return useConsulenzaMutation(
    async (propostaId: string) =>
      (
        await api.post<Consulenza>(
          `/me/consulenze/${requestId}/proposte/${propostaId}/rifiuta`,
        )
      ).data,
  );
}

export function useAnnullaConsulenza(requestId: string) {
  return useConsulenzaMutation<void>(async () =>
    (await api.post<Consulenza>(`/me/consulenze/${requestId}/annulla`)).data,
  );
}

export function usePrenotaSlot(requestId: string) {
  return useConsulenzaMutation(
    async (slotId: string) =>
      (
        await api.post<Consulenza>(`/me/consulenze/${requestId}/prenota`, {
          slot_id: slotId,
        })
      ).data,
  );
}

export function useAnnullaPrenotazione(requestId: string) {
  return useConsulenzaMutation<void>(async () =>
    (await api.post<Consulenza>(`/me/consulenze/${requestId}/prenotazione/annulla`)).data,
  );
}
