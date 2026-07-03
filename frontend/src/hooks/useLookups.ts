import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Lookups } from "../types";
import { useAuth } from "./useAuth";

export function useLookups() {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["lookups"],
    queryFn: async () => (await api.get<Lookups>("/lookups")).data,
    enabled: !!session,
    staleTime: 60 * 60_000,
  });
}
