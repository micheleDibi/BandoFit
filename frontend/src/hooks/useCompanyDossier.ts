import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { DossierResponse, ImportPreview, ImportResult } from "../types";
import { useAuth } from "./useAuth";

/** Deadline del polling lato server (240s, vedi backend/app/clients/openapi.py)
 *  più un margine. Senza timeout esplicito axios attende all'infinito e una rete
 *  caduta a metà chiamata lascerebbe la modale a girare per sempre. */
const PREVIEW_TIMEOUT_MS = 255_000;

export function useCompanyDossier() {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["company-dossier"],
    queryFn: async () => (await api.get<DossierResponse>("/me/company/dossier")).data,
    enabled: !!session,
    staleTime: 60_000,
  });
}

/** Fase 1: recupera i dati dal Registro Imprese (A PAGAMENTO lato server) e li
 *  mostra in anteprima. NON scrive nulla: il bottone che la lancia mostra
 *  sempre la nota costo. */
export function usePreviewImport() {
  return useMutation({
    mutationFn: async (partitaIva: string) =>
      (
        await api.post<ImportPreview>(
          "/me/company/import/preview",
          { partita_iva: partitaIva },
          { timeout: PREVIEW_TIMEOUT_MS },
        )
      ).data,
  });
}

/** Fase 2: scrive i dati già recuperati. Gratuita e rapida — nessuna chiamata
 *  al provider, quindi nessun timeout dedicato. */
export function useConfirmImport() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (partitaIva: string) =>
      (await api.post<ImportResult>("/me/company/import/confirm", { partita_iva: partitaIva })).data,
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
