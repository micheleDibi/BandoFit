import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "../lib/api";
import { useAuth } from "./useAuth";
import type { NotifichePage } from "../types";

/** Prima pagina di notifiche + conteggio non lette, in polling: il repo non
 *  usa websocket/realtime, il polling è l'idioma già adottato per l'AI-check. */
export function useNotifications() {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["notifications"],
    queryFn: async () => (await api.get<NotifichePage>("/me/notifications")).data,
    enabled: !!session,
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}

/** Pagina di notifiche per il centro alert (`/app/notifiche`): paginata e
 *  filtrabile per azienda (Advisor). `companyId` null = tutte le aziende; il
 *  conteggio non-lette resta comunque aggregato (lo decide il backend). */
export function useNotificationsPage(page: number, companyId: string | null) {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["notifications-page", page, companyId],
    queryFn: async () =>
      (
        await api.get<NotifichePage>("/me/notifications", {
          params: { page, ...(companyId ? { company_id: companyId } : {}) },
        })
      ).data,
    enabled: !!session,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

export function useMarkNotificationsRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { all?: boolean; ids?: number[] }) => {
      await api.post("/me/notifications/read", payload);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
      queryClient.invalidateQueries({ queryKey: ["notifications-page"] });
    },
  });
}
