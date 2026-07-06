import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { DocumentsResponse } from "../types";
import { useAuth } from "./useAuth";

export function useCompanyDocuments() {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["company-documents"],
    queryFn: async () => (await api.get<DocumentsResponse>("/me/company/documents")).data,
    enabled: !!session,
    staleTime: 30_000,
    // Finché c'è una richiesta in lavorazione, il backend la completa a ogni
    // lettura (poll gratuito): ricontrolliamo ogni 10s.
    refetchInterval: (query) =>
      query.state.data?.documents.some((d) => d.status === "pending") ? 10_000 : false,
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
  const response = await api.get(`/me/company/documents/${documentId}/file`, {
    responseType: "blob",
  });
  const url = URL.createObjectURL(response.data as Blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName || "visura.pdf";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
