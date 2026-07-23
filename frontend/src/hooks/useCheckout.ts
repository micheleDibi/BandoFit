import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "../lib/api";
import type { CheckoutPreview, CheckoutResult, Page, Purchase } from "../types";
import { useAuth } from "./useAuth";

/** Cosa si sta acquistando: uno e uno solo tra piano e addon. */
export interface CheckoutTarget {
  plan_slug?: string;
  addon_slug?: string;
  /** Unità (solo addon, 1..100); omessa = 1. */
  quantita?: number;
}

export function useCheckoutPreview(target: CheckoutTarget) {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["checkout-preview", target.plan_slug ?? null, target.addon_slug ?? null, target.quantita ?? 1],
    // POST ma puro (nessun effetto): resta una query, non una mutation.
    queryFn: async () =>
      (await api.post<CheckoutPreview>("/me/checkout/preview", target)).data,
    enabled: !!session && (!!target.plan_slug || !!target.addon_slug),
    staleTime: 60_000,
  });
}

export function useStartCheckout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: CheckoutTarget & { auto_renew: boolean }) =>
      (await api.post<CheckoutResult>("/me/checkout", data)).data,
    // Nasce un purchase in_attesa: lo storico va rinfrescato.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["purchases"] }),
  });
}

/** Dettaglio di un acquisto. Con `poll` attivo si interroga a intervallo
 *  finché lo stato è in_attesa: al primo stato finale si spegne da solo. */
export function usePurchase(purchaseId: string | undefined, poll: boolean, intervalMs: number) {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["purchases", purchaseId],
    queryFn: async () => (await api.get<Purchase>(`/me/purchases/${purchaseId}`)).data,
    enabled: !!session && !!purchaseId,
    refetchInterval: (query) =>
      poll && (query.state.data === undefined || query.state.data.status === "in_attesa")
        ? intervalMs
        : false,
  });
}

/** Riconciliazione on-demand col provider («Verifica ora»): idempotente,
 *  stessa strada del webhook. */
export function useSyncPurchase() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (purchaseId: string) =>
      (await api.post<Purchase>(`/me/purchases/${purchaseId}/sync`)).data,
    onSuccess: (purchase) => {
      queryClient.setQueryData(["purchases", purchase.id], purchase);
      queryClient.invalidateQueries({ queryKey: ["purchases", "page"] });
      // Un pagamento confermato può aver cambiato piano o scadenza.
      if (purchase.status === "pagato") {
        queryClient.invalidateQueries({ queryKey: ["me"] });
      }
    },
  });
}

export function usePurchases(page: number) {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["purchases", "page", page],
    queryFn: async () =>
      (await api.get<Page<Purchase>>("/me/purchases", { params: { page } })).data,
    enabled: !!session,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}
