import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Vocabolario } from "../types";
import { useAuth } from "./useAuth";
import { useFunzioni } from "./useFunzioni";

/** Vocabolario controllato del modulo partenariati (tipi di soggetto,
 *  competenze, forme di aggregazione, ruoli): cambia solo con un rilascio,
 *  quindi resta in cache un'ora. */
export function usePartenariatiVocabolario() {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  return useQuery({
    queryKey: ["partenariati", "vocabolario"],
    queryFn: async () => (await api.get<Vocabolario>("/partenariati/vocabolario")).data,
    enabled: !!session && partenariatiAttivo,
    staleTime: 60 * 60_000,
  });
}
