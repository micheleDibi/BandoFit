import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Plan } from "../types";

export function usePlans() {
  return useQuery({
    queryKey: ["plans"],
    queryFn: async () => (await api.get<Plan[]>("/plans")).data,
    staleTime: 5 * 60_000,
  });
}
