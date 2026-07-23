import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Companies, CompanySummary } from "../types";
import { useMe } from "./useMe";

/** Elenco delle aziende gestite (Advisor, pagina /app/aziende). Abilitato
 *  solo quando il piano ne prevede più di una. Endpoint owner-only. */
export function useCompanies() {
  const { data: me } = useMe();
  const isMulti = (me?.max_aziende ?? 1) > 1;
  return useQuery({
    queryKey: ["companies"],
    queryFn: async () => (await api.get<Companies>("/me/aziende")).data,
    enabled: isMulti,
    staleTime: 60_000,
  });
}

/** Le aziende su cui l'utente può OPERARE, per lo switcher (0031): per un
 *  membro attivo è la sua visibilità ∩ vive; per i titolari le proprie.
 *  Gated dal flag child-aware `me.multi_azienda`. */
export function useVisibleCompanies() {
  const { data: me } = useMe();
  return useQuery({
    queryKey: ["companies", "visibili"],
    queryFn: async () => (await api.get<CompanySummary[]>("/me/aziende/visibili")).data,
    enabled: !!me?.multi_azienda,
    staleTime: 60_000,
  });
}

export function useCreateCompany() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: { ragione_sociale: string; partita_iva: string }) =>
      (await api.post<CompanySummary>("/me/aziende", data)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["companies"] });
    },
  });
}

export function useDeleteCompany() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (companyId: string) => {
      await api.delete(`/me/aziende/${companyId}`);
      return companyId;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["companies"] });
    },
  });
}
