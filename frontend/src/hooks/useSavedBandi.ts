import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Page, SavedBandoItem } from "../types";
import { useAuth } from "./useAuth";

interface SavedIds {
  bando_ids: number[];
}

/** Elenco paginato dei bandi salvati (i più recenti per primi). */
export function useSavedBandi(page: number) {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["saved-bandi", page],
    queryFn: async () =>
      (await api.get<Page<SavedBandoItem>>("/me/saved-bandi", { params: { page, page_size: 20 } }))
        .data,
    enabled: !!session,
    placeholderData: keepPreviousData,
  });
}

/** Id dei bandi salvati come Set: stato O(1) dei toggle nelle liste. */
export function useSavedIds() {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["saved-bandi", "ids"],
    queryFn: async () => (await api.get<SavedIds>("/me/saved-bandi/ids")).data,
    enabled: !!session,
    staleTime: 60_000,
    select: (data) => new Set(data.bando_ids),
  });
}

/** Salva/rimuovi un bando: aggiornamento OTTIMISTA del solo Set degli id
 *  (il bookmark è una micro-interazione: uno spinner sembrerebbe rotto),
 *  la lista paginata si riallinea con l'invalidazione. */
export function useToggleSaved() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ bando, save }: { bando: { id: number; slug: string }; save: boolean }) => {
      if (save) {
        await api.post("/me/saved-bandi", { bando_slug: bando.slug });
      } else {
        await api.delete(`/me/saved-bandi/${bando.id}`);
      }
    },
    onMutate: async ({ bando, save }) => {
      await queryClient.cancelQueries({ queryKey: ["saved-bandi", "ids"] });
      const previous = queryClient.getQueryData<SavedIds>(["saved-bandi", "ids"]);
      queryClient.setQueryData<SavedIds>(["saved-bandi", "ids"], (old) => {
        const ids = new Set(old?.bando_ids ?? []);
        if (save) ids.add(bando.id);
        else ids.delete(bando.id);
        return { bando_ids: [...ids] };
      });
      return { previous };
    },
    onError: (_err, _vars, context) => {
      if (context?.previous !== undefined) {
        queryClient.setQueryData(["saved-bandi", "ids"], context.previous);
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["saved-bandi"] });
    },
  });
}
