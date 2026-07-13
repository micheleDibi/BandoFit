import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useAuth } from "./useAuth";
import type { Slot } from "../types";

export interface SlotPayload {
  inizio: string; // ISO UTC
  fine: string;
}

/** Slot di disponibilità del progettista autenticato (futuri, con flag
 *  prenotato). `enabled` permette al Calendario di non chiamare l'endpoint
 *  (403) quando l'utente non è un progettista. */
export function useSlots(enabled = true) {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["progettista-slots"],
    queryFn: async () => (await api.get<Slot[]>("/progettista/slots")).data,
    enabled: !!session && enabled,
  });
}

function useInvalidateSlots() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["progettista-slots"] });
}

export function useCreateSlot() {
  const invalidate = useInvalidateSlots();
  return useMutation({
    mutationFn: async (payload: SlotPayload) =>
      (await api.post<Slot>("/progettista/slots", payload)).data,
    onSuccess: invalidate,
  });
}

export function useUpdateSlot() {
  const invalidate = useInvalidateSlots();
  return useMutation({
    mutationFn: async ({ slotId, ...payload }: SlotPayload & { slotId: string }) =>
      (await api.patch<Slot>(`/progettista/slots/${slotId}`, payload)).data,
    onSuccess: invalidate,
  });
}

export function useDeleteSlot() {
  const invalidate = useInvalidateSlots();
  return useMutation({
    mutationFn: async (slotId: string) => {
      await api.delete(`/progettista/slots/${slotId}`);
    },
    onSuccess: invalidate,
  });
}

export interface SerieCreatePayload {
  /** Occorrenze già materializzate nel fuso del browser (lib/ricorrenza.ts). */
  occorrenze: SlotPayload[];
}

export interface SerieCreateResult {
  serie_id: string;
  creati: Slot[];
  /** Occorrenze scartate perché sovrapposte a disponibilità esistenti. */
  saltati: number;
}

export interface SerieDeleteResult {
  eliminati: number;
  /** Slot prenotati: l'eliminazione della serie non li tocca mai. */
  mantenuti: number;
}

export function useCreateSlotSerie() {
  const invalidate = useInvalidateSlots();
  return useMutation({
    mutationFn: async (payload: SerieCreatePayload) =>
      (await api.post<SerieCreateResult>("/progettista/slots/serie", payload)).data,
    onSuccess: invalidate,
  });
}

export function useDeleteSlotSerie() {
  const invalidate = useInvalidateSlots();
  return useMutation({
    mutationFn: async (serieId: string) =>
      (await api.delete<SerieDeleteResult>(`/progettista/slots/serie/${serieId}`)).data,
    onSuccess: invalidate,
  });
}
