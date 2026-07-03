import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { BandoDetail, BandoListItem, Page } from "../types";
import { toApiParams, type BandiFilterState } from "./useBandiFilters";

export function useBandi(filters: BandiFilterState) {
  const params = toApiParams(filters);
  return useQuery({
    queryKey: ["bandi", params],
    queryFn: async () =>
      (await api.get<Page<BandoListItem>>("/bandi", { params })).data,
    placeholderData: keepPreviousData,
  });
}

export function useBando(slug: string | undefined) {
  return useQuery({
    queryKey: ["bando", slug],
    queryFn: async () => (await api.get<BandoDetail>(`/bandi/${slug}`)).data,
    enabled: !!slug,
    staleTime: 5 * 60_000,
  });
}
