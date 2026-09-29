import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiErrorCode } from "../lib/api";
import { useAuth } from "./useAuth";
import type {
  AppuntamentoProgettista,
  CallVistaProgettista,
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
 *  server in audit_log — si carica su richiesta esplicita, non in eager. Come
 *  per la call (`useCallRichiesta`), la chiave sta FUORI dalla radice
 *  `["progettista-richieste"]` e il focus della finestra non la rilegge: una
 *  lettura (e un accesso registrato) solo per un'azione di chi la apre. */
export function useDossierRichiesta(requestId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["progettista-dossier-richiesta", requestId],
    queryFn: async () =>
      (await api.get<FullCompany>(`/progettista/richieste/${requestId}/dossier`)).data,
    enabled,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
}

/** La call di partenariato di un consulto chiesto dalla call (WP9), solo per
 *  il progettista assegnato. Ogni lettura è registrata lato server PRIMA di
 *  rispondere (se la registrazione fallisce: 502 e nessun dato): si carica su
 *  richiesta esplicita, come il dossier. La chiave sta FUORI dalla radice
 *  `["progettista-richieste"]`: le azioni che la invalidano (ritiro di una
 *  proposta, annullamento di un appuntamento) altrimenti rileggerebbero la
 *  call e scriverebbero un nuovo accesso nell'audit a ogni azione. */
export function useCallRichiesta(requestId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["progettista-call-richiesta", requestId],
    queryFn: async () =>
      (await api.get<CallVistaProgettista>(`/progettista/richieste/${requestId}/call`)).data,
    enabled,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    // 403/404 non cambiano riprovando; un 502 (registrazione non riuscita) sì.
    retry: (tentativi, errore) => {
      const codice = apiErrorCode(errore);
      return codice !== "not_found" && codice !== "forbidden" && tentativi < 1;
    },
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
