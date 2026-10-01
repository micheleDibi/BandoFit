import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Lookups } from "../types";
import { useAuth } from "./useAuth";

/** Liste tutte vuote: il catalogo non ha risposto (il backend degrada a
 *  liste vuote invece di fallire). Un campo null o assente conta come vuoto. */
function tutteVuote(lookups: Lookups | undefined): boolean {
  return (
    !!lookups &&
    Object.values(lookups).every((lista: unknown) => !Array.isArray(lista) || lista.length === 0)
  );
}

export function useLookups() {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["lookups"],
    queryFn: async () => (await api.get<Lookups>("/lookups")).data,
    enabled: !!session,
    // Un'ora di norma; con le liste tutte vuote (catalogo degradato) un minuto,
    // così i filtri tornano appena il catalogo risponde.
    staleTime: (query) => (tutteVuote(query.state.data) ? 60_000 : 60 * 60_000),
  });
}
