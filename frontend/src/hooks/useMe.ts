import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Me } from "../types";
import { useAuth } from "./useAuth";

export function useMe() {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => (await api.get<Me>("/me")).data,
    enabled: !!session,
    staleTime: 60_000,
  });
}

export function useUpdateProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: {
      nome?: string;
      cognome?: string;
      azienda?: string;
      // Omessi quando invariati: il server valida solo le chiavi presenti
      // (i telefoni legacy non in E.164 restano intatti finché non cambiano).
      telefono?: string | null;
      job_position_id?: number | null;
      job_position_altro?: string | null;
      codice_fiscale?: string | null;
    }) => (await api.patch<Me>("/me", data)).data,
    onSuccess: (me) => queryClient.setQueryData(["me"], me),
  });
}

/** Verifica del codice fiscale all'Anagrafe Tributaria (operazione A
 * PAGAMENTO lato server: il bottone mostra sempre la nota costo). */
export function useVerifyCf() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (codiceFiscale: string) =>
      (
        await api.post<{ codice_fiscale: string; cf_verified_at: string | null }>(
          "/me/verify-cf",
          { codice_fiscale: codiceFiscale },
        )
      ).data,
    onSuccess: (result) => {
      queryClient.setQueryData<Me | undefined>(["me"], (me) =>
        me
          ? {
              ...me,
              profile: {
                ...me.profile,
                codice_fiscale: result.codice_fiscale,
                cf_verified_at: result.cf_verified_at,
              },
            }
          : me,
      );
    },
  });
}

export function useSwitchPlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (planId: number) =>
      (await api.post<Me>("/me/subscription", { plan_id: planId })).data,
    onSuccess: (me) => {
      queryClient.setQueryData(["me"], me);
      // Un downgrade può aver retrocesso membri: la FamilyCard va aggiornata.
      queryClient.invalidateQueries({ queryKey: ["family"] });
    },
  });
}
