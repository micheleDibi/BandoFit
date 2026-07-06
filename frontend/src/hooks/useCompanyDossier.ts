import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { DossierResponse, ImportResult } from "../types";
import { useAuth } from "./useAuth";

export function useCompanyDossier() {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["company-dossier"],
    queryFn: async () => (await api.get<DossierResponse>("/me/company/dossier")).data,
    enabled: !!session,
    staleTime: 60_000,
  });
}

/** Import della visura da openapi.it (operazione A PAGAMENTO lato server:
 * il bottone che la lancia mostra sempre la nota costo). */
export function useImportCompany() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (partitaIva: string) =>
      (await api.post<ImportResult>("/me/company/import", { partita_iva: partitaIva })).data,
    onSuccess: (result) => {
      queryClient.setQueryData(["company"], result.company);
      queryClient.setQueryData<DossierResponse>(["company-dossier"], {
        editable: result.company.editable,
        imported: true,
        fetched_at: result.fetched_at,
        sandbox: result.sandbox,
        dossier: result.dossier,
        people: result.people,
        derived: {},
      });
      // derived viene ricalcolato dal server: riallineiamo in background.
      queryClient.invalidateQueries({ queryKey: ["company-dossier"] });
    },
  });
}
