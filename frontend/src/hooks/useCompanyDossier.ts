import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { DossierResponse, ImportPreview, ImportResult } from "../types";
import { useAuth } from "./useAuth";

/** Catena dei tempi dell'anteprima: il server chiude entro 277 s (IT-full con
 *  deadline 240 s, poi IT-advanced solo se sono passati al massimo 250 s e con
 *  un tetto di 25 s) < frontend 290 s < TTL del lock di import 330 s. Senza
 *  timeout esplicito axios attende all'infinito e una rete caduta a metà
 *  chiamata lascerebbe la modale a girare per sempre. */
const PREVIEW_TIMEOUT_MS = 290_000;

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
      // L'import è l'azione che popola sedi e ATECO secondari: i facet cambiano.
      queryClient.invalidateQueries({ queryKey: ["company-facets"] });
      // La conferma salva anche i bilanci recuperati con l'anteprima.
      queryClient.invalidateQueries({ queryKey: ["company-bilanci"] });
    },
  });
}
