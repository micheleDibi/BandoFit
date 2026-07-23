import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Entitlements } from "../types";
import { useAuth } from "./useAuth";

/** Le quote dell'account (seats, aziende, AI-check) dalla fonte unica
 *  `GET /me/entitlements` (0030): il frontend legge, non ricalcola mai.
 *  Invalidare `["entitlements"]` dopo acquisti/inviti/cambi piano. */
export function useEntitlements() {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["entitlements"],
    queryFn: async () => (await api.get<Entitlements>("/me/entitlements")).data,
    enabled: !!session,
    staleTime: 30_000,
  });
}
