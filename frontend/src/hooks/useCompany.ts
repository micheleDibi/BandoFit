import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { CompanyResponse } from "../types";
import { useAuth } from "./useAuth";

export function useCompany() {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["company"],
    queryFn: async () => (await api.get<CompanyResponse>("/me/company")).data,
    enabled: !!session,
    staleTime: 60_000,
  });
}

export interface CompanyPayload {
  ragione_sociale: string;
  forma_giuridica: string | null;
  partita_iva: string;
  codice_fiscale: string | null;
  ateco_id: number | null;
  settore_id: number | null;
  regione_id: number | null;
  beneficiari_ids: number[];
  anno_fondazione: number | null;
  indirizzo: string | null;
  comune: string | null;
  provincia: string | null;
  cap: string | null;
  classe_dimensionale: string | null;
  numero_dipendenti: number | null;
  fascia_fatturato: string | null;
  pec: string | null;
  telefono: string | null;
  sito_web: string | null;
}

export function useSaveCompany() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: CompanyPayload) =>
      (await api.put<CompanyResponse>("/me/company", data)).data,
    onSuccess: (response) => queryClient.setQueryData(["company"], response),
  });
}
