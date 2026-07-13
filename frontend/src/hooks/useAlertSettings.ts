import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { AlertSettings } from "../types";
import { useAuth } from "./useAuth";

/** Avvisi email sui nuovi bandi: stessa fonte di verità del link di
 *  disiscrizione nelle email (bando_alert_settings). */
export function useAlertSettings() {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["alert-settings"],
    queryFn: async () => (await api.get<AlertSettings>("/me/alert-settings")).data,
    enabled: !!session,
    staleTime: 60_000,
  });
}

export function useSaveAlertSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: { abilitati: boolean }) =>
      (await api.put<AlertSettings>("/me/alert-settings", data)).data,
    onSuccess: (saved) => queryClient.setQueryData(["alert-settings"], saved),
  });
}
