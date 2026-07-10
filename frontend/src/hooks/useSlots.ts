import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useAuth } from "./useAuth";
import type { Slot } from "../types";

export interface SlotPayload {
  inizio: string; // ISO UTC
  fine: string;
}

/** Slot di disponibilità del progettista autenticato (futuri, con flag prenotato). */
export function useSlots() {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["progettista-slots"],
    queryFn: async () => (await api.get<Slot[]>("/progettista/slots")).data,
    enabled: !!session,
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
