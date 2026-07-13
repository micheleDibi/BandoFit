import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useAuth } from "./useAuth";
import type {
  AppuntamentoProgettista,
  FullCompany,
  RichiestaPoolDetail,
  RichiestePool,
} from "../types";

/** Pool delle richieste aperte + quelle assegnate al progettista. */
export function useRichiestePool() {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["progettista-richieste"],
    queryFn: async () => (await api.get<RichiestePool>("/progettista/richieste")).data,
    enabled: !!session,
  });
}

export function useRichiesta(requestId: string | undefined) {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["progettista-richieste", requestId],
    queryFn: async () =>
      (await api.get<RichiestaPoolDetail>(`/progettista/richieste/${requestId}`)).data,
    enabled: !!session && !!requestId,
  });
}

export function useInviaProposta(requestId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (messaggio: string) =>
      (
        await api.post<RichiestaPoolDetail>(
          `/progettista/richieste/${requestId}/proposte`,
          { messaggio },
        )
      ).data,
    onSuccess: (data) => {
      queryClient.setQueryData(["progettista-richieste", data.id], data);
      queryClient.invalidateQueries({ queryKey: ["progettista-richieste"], exact: true });
    },
  });
}

export function useRitiraProposta() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (propostaId: string) => {
      await api.post(`/progettista/proposte/${propostaId}/ritira`);
    },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["progettista-richieste"] }),
  });
}

/** Vista FULL (solo dopo l'assegnazione): ogni lettura è registrata lato
 *  server in audit_log — si carica su richiesta esplicita, non in eager. */
export function useDossierRichiesta(requestId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["progettista-richieste", requestId, "dossier"],
    queryFn: async () =>
      (await api.get<FullCompany>(`/progettista/richieste/${requestId}/dossier`)).data,
    enabled,
    staleTime: 5 * 60_000,
  });
}

/** Appuntamenti confermati del progettista. `enabled` permette al Calendario
 *  di non chiamare l'endpoint (403) quando l'utente non è un progettista. */
export function useAppuntamenti(enabled = true) {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["progettista-appuntamenti"],
    queryFn: async () =>
      (await api.get<AppuntamentoProgettista[]>("/progettista/appuntamenti")).data,
    enabled: !!session && enabled,
  });
}

export function useAnnullaAppuntamento() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (bookingId: string) => {
      await api.post(`/progettista/appuntamenti/${bookingId}/annulla`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["progettista-appuntamenti"] });
      queryClient.invalidateQueries({ queryKey: ["progettista-richieste"] });
      queryClient.invalidateQueries({ queryKey: ["progettista-slots"] });
    },
  });
}
