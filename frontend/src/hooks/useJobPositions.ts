import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { JobPosition } from "../types";

/** Lookup delle posizioni aziendali: endpoint PUBBLICO (serve anche alla
 *  registrazione, senza sessione), catalogo stabile → cache lunga. */
export function useJobPositions() {
  return useQuery({
    queryKey: ["job-positions"],
    queryFn: async () => (await api.get<JobPosition[]>("/job-positions")).data,
    staleTime: 60 * 60_000,
  });
}
