import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { AiChecksResponse } from "../types";
import { useAuth } from "./useAuth";

// L'analisi dura di solito 1-2 minuti; il backend chiude comunque come errore
// le pending oltre i 10 minuti, quindi il polling non resta acceso per sempre.
const RECENT_PENDING_WINDOW_MS = 12 * 60_000;

/** Storico AI-check di un bando (il primo elemento è il più recente, con
 *  report completo). Fa polling ogni 4s finché c'è un'analisi in corso. */
export function useAiChecksForBando(slug: string | undefined) {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["ai-check", slug],
    queryFn: async () =>
      (
        await api.get<AiChecksResponse>("/me/ai-checks", {
          params: { bando_slug: slug, page_size: 10 },
        })
      ).data,
    enabled: !!session && !!slug,
    staleTime: 15_000,
    refetchInterval: (query) =>
      query.state.data?.items.some(
        (c) =>
          c.status === "pending" &&
          Date.now() - new Date(c.created_at).getTime() < RECENT_PENDING_WINDOW_MS,
      )
        ? 4_000
        : false,
  });
}

/** Storico AI-check di tutta l'azienda (senza report, per le liste).
 *  Stesso polling della vista per bando (più blando): un'analisi avviata
 *  altrove deve aggiornarsi anche restando sulla pagina Azienda. */
export function useAiChecks() {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["ai-checks"],
    queryFn: async () =>
      (await api.get<AiChecksResponse>("/me/ai-checks", { params: { page_size: 20 } })).data,
    enabled: !!session,
    staleTime: 30_000,
    refetchInterval: (query) =>
      query.state.data?.items.some(
        (c) =>
          c.status === "pending" &&
          Date.now() - new Date(c.created_at).getTime() < RECENT_PENDING_WINDOW_MS,
      )
        ? 10_000
        : false,
  });
}

/** Avvia l'analisi (consuma 1 AI-check del piano; operazione A PAGAMENTO lato server). */
export function useRequestAiCheck(slug: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      (await api.post("/me/ai-checks", { bando_slug: slug })).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ai-check", slug] });
      queryClient.invalidateQueries({ queryKey: ["ai-checks"] });
    },
  });
}
