import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Addon } from "../types";
import { useAuth } from "./useAuth";

/** Add-on attivi (catalogo cliente). A differenza di /plans la rotta è
 *  autenticata: il catalogo si vede solo dentro l'app. */
export function useAddons() {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["addons"],
    queryFn: async () => (await api.get<Addon[]>("/addons")).data,
    enabled: !!session,
    staleTime: 5 * 60_000,
  });
}
