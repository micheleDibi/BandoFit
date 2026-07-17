import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { BillingPrefill, BillingProfile, BillingProfileInput } from "../types";
import { useAuth } from "./useAuth";

export function useBillingProfile() {
  const { session } = useAuth();
  return useQuery({
    // null = anagrafica mai compilata (non è un errore: il form parte vuoto).
    queryKey: ["billing-profile"],
    queryFn: async () => (await api.get<BillingProfile | null>("/me/billing-profile")).data,
    enabled: !!session,
    staleTime: 60_000,
  });
}

/** Proposta di precompilazione dai dati aziendali: si chiede solo quando serve
 *  (profilo mai compilato) e non viene mai persistita finché l'utente non salva. */
export function useBillingPrefill(enabled: boolean) {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["billing-prefill"],
    queryFn: async () => (await api.get<BillingPrefill>("/me/billing-profile/prefill")).data,
    enabled: !!session && enabled,
    staleTime: 60_000,
  });
}

export function useSaveBillingProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: BillingProfileInput) =>
      (await api.put<BillingProfile>("/me/billing-profile", data)).data,
    // Il PUT restituisce il profilo salvato (con l'esito VIES): è già il dato
    // fresco della query, niente refetch.
    onSuccess: (saved) => queryClient.setQueryData(["billing-profile"], saved),
  });
}
