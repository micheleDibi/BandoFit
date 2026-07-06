import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Preferences } from "../types";
import { useAuth } from "./useAuth";

export const EMPTY_PREFERENCES: Preferences = {
  regioni: [],
  settori: [],
  beneficiari: [],
  codici_ateco: [],
  tipologie: [],
  modalita: [],
  programmi: [],
};

export function usePreferences() {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["preferences"],
    queryFn: async () => (await api.get<Preferences>("/me/preferences")).data,
    enabled: !!session,
    staleTime: 60_000,
  });
}

export function useSavePreferences() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: Preferences) =>
      (await api.put<Preferences>("/me/preferences", data)).data,
    onSuccess: (saved) => queryClient.setQueryData(["preferences"], saved),
  });
}
