import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { api } from "../lib/api";
import type { DocumentsResponse } from "../types";
import { useAuth } from "./useAuth";

const RECENT_PENDING_WINDOW_MS = 15 * 60_000;

export function useCompanyDocuments() {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["company-documents"],
    queryFn: async () => (await api.get<DocumentsResponse>("/me/company/documents")).data,
    enabled: !!session,
    staleTime: 30_000,
    // Finché c'è una richiesta RECENTE in lavorazione, il backend la completa
    // a ogni lettura (poll gratuito): ricontrolliamo ogni 10s. Le pending
    // vecchie non tengono la pagina in polling perpetuo (il backend le chiude
    // comunque come errore dopo 24h).
    refetchInterval: (query) =>
      query.state.data?.documents.some(
        (d) =>
          d.status === "pending" &&
          Date.now() - new Date(d.created_at).getTime() < RECENT_PENDING_WINDOW_MS,
      )
        ? 10_000
        : false,
  });
}

/** Richiesta della visura ufficiale (operazione A PAGAMENTO lato server). */
export function useRequestDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => (await api.post("/me/company/documents")).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["company-documents"] }),
  });
}

/** Scarica il PDF e avvia il download nel browser. */
export async function downloadDocumentFile(documentId: string, fileName: string) {
  let response;
  try {
    response = await api.get(`/me/company/documents/${documentId}/file`, {
      responseType: "blob",
    });
  } catch (err) {
    // Con responseType blob anche il body d'errore JSON arriva come Blob:
    // lo riconvertiamo, così apiErrorMessage mostra il messaggio vero.
    if (isAxiosError(err) && err.response && err.response.data instanceof Blob) {
      try {
        err.response.data = JSON.parse(await err.response.data.text());
      } catch {
        // non era JSON: resta il messaggio generico
      }
    }
    throw err;
  }
  const url = URL.createObjectURL(response.data as Blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName || "visura.pdf";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
