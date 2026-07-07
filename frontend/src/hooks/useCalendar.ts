import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { CalendarEvent } from "../types";
import { useAuth } from "./useAuth";

export interface CalendarEventPayload {
  titolo: string;
  data: string; // YYYY-MM-DD
  tutto_il_giorno: boolean;
  ora_inizio: string | null;
  ora_fine: string | null;
  note: string | null;
}

/** Eventi del mese (le modifiche possono spostare eventi tra mesi:
 *  le mutazioni invalidano l'intero prefisso ["calendar"]). */
export function useCalendarEvents(anno: number, mese: number) {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["calendar", anno, mese],
    queryFn: async () =>
      (await api.get<{ items: CalendarEvent[] }>("/me/calendar", { params: { anno, mese } }))
        .data.items,
    enabled: !!session,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

export function useCreateEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CalendarEventPayload) =>
      (await api.post<CalendarEvent>("/me/calendar", payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["calendar"] }),
  });
}

export function useUpdateEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, patch }: { id: string; patch: Partial<CalendarEventPayload> }) =>
      (await api.patch<CalendarEvent>(`/me/calendar/${id}`, patch)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["calendar"] }),
  });
}

export function useDeleteEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await api.delete(`/me/calendar/${id}`);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["calendar"] }),
  });
}

/** Aggiunge la scadenza di un bando al calendario (evento tipo 'bando'). */
export function useAddBandoDeadline() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (bandoSlug: string) =>
      (await api.post<CalendarEvent>("/me/calendar/bando", { bando_slug: bandoSlug })).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar"] });
      queryClient.invalidateQueries({ queryKey: ["saved-bandi"] }); // aggiorna in_calendario
    },
  });
}
