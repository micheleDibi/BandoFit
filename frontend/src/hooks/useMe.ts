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
      telefono?: string;
    }) => (await api.patch<Me>("/me", data)).data,
    onSuccess: (me) => queryClient.setQueryData(["me"], me),
  });
}

export function useSwitchPlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (planId: number) =>
      (await api.post<Me>("/me/subscription", { plan_id: planId })).data,
    onSuccess: (me) => queryClient.setQueryData(["me"], me),
  });
}
