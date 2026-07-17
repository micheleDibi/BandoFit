import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { SubscriptionManagement } from "../types";
import { useAuth } from "./useAuth";

const KEY = ["subscription-management"];

/** Stato di rinnovo, metodo salvato e cambio programmato. Con `pollMetodo`
 *  attivo si interroga a intervallo finché il metodo non compare: è il
 *  fallback al webhook dopo il salvataggio carta nel widget. */
export function useSubscriptionManagement(pollMetodo = false) {
  const { session } = useAuth();
  return useQuery({
    queryKey: KEY,
    queryFn: async () =>
      (await api.get<SubscriptionManagement>("/me/subscription/management")).data,
    enabled: !!session,
    staleTime: 30_000,
    refetchInterval: (query) =>
      pollMetodo && !query.state.data?.metodo.presente ? 2_000 : false,
  });
}

/** Le mutation restituiscono tutte lo stato aggiornato: si scrive in cache,
 *  niente refetch. */
function useMgmtMutation<TVars>(mutationFn: (vars: TVars) => Promise<SubscriptionManagement>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: (out) => queryClient.setQueryData(KEY, out),
  });
}

export function useSetAutoRenew() {
  return useMgmtMutation(async (enabled: boolean) =>
    (await api.post<SubscriptionManagement>("/me/subscription/auto-renew", { enabled })).data,
  );
}

/** Programma il passaggio a un piano inferiore (Gratuito = disdetta) alla
 *  scadenza: fino a quel giorno resta tutto attivo. */
export function useScheduleDowngrade() {
  return useMgmtMutation(async (planSlug: string) =>
    (
      await api.post<SubscriptionManagement>("/me/subscription/downgrade", {
        plan_slug: planSlug,
      })
    ).data,
  );
}

export function useCancelScheduledChange() {
  return useMgmtMutation(async (_: void) =>
    (await api.delete<SubscriptionManagement>("/me/subscription/scheduled-change")).data,
  );
}

/** Ordine a 0 € per salvare una carta senza acquisto: il token apre il widget
 *  (savePaymentMethodFor: 'merchant'); il metodo si persiste lato server. */
export function useStartAddMethod() {
  return useMutation({
    mutationFn: async () =>
      (await api.post<{ revolut_order_token: string }>("/me/payment-method")).data,
  });
}

/** Revoca il metodo salvato (e il backend spegne il rinnovo automatico). */
export function useRemoveMethod() {
  return useMgmtMutation(async (_: void) =>
    (await api.delete<SubscriptionManagement>("/me/payment-method")).data,
  );
}
