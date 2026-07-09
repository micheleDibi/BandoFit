import type { FacetKey } from "../hooks/useBandiFilters";
import type { CompanyFacets, Preferences } from "../types";

const union = (...groups: Array<number | number[] | null | undefined>): number[] => {
  const out = new Set<number>();
  for (const group of groups) {
    if (group === null || group === undefined) continue;
    for (const id of Array.isArray(group) ? group : [group]) out.add(id);
  }
  return [...out].sort((a, b) => a - b);
};

/** Preset «Bandi per te»: unione dei valori REALI dell'azienda e delle
 *  preferenze personali dell'utente, nelle chiavi dei filtri URL.
 *
 *  I valori dell'azienda arrivano dai FACET (`GET /me/company/facets`), non
 *  dai campi del form: un'azienda con tre unità locali è ammissibile in tre
 *  regioni, e opera in tutte le divisioni ATECO che il Registro le riconosce.
 *  Filtrare sulla sola sede legale nascondeva bandi per cui è candidabile. */
export function buildBandiPerTePreset(
  facets: CompanyFacets | null | undefined,
  preferences: Preferences | null | undefined,
): Record<FacetKey, number[]> {
  return {
    regioni: union(facets?.regioni, preferences?.regioni),
    settori: union(facets?.settori, preferences?.settori),
    ateco: union(facets?.ateco, preferences?.codici_ateco),
    // Dichiarate sui dati aziendali: ereditate come regioni/settore/ATECO.
    beneficiari: union(facets?.beneficiari, preferences?.beneficiari),
    tipologie: union(preferences?.tipologie),
    modalita: union(preferences?.modalita),
    programmi: union(preferences?.programmi),
  };
}

export function presetHasValues(preset: Record<FacetKey, number[]>): boolean {
  return Object.values(preset).some((ids) => ids.length > 0);
}

/** Query string per /app/bandi con il preset applicato. */
export function presetSearchParams(preset: Record<FacetKey, number[]>): string {
  const params = new URLSearchParams();
  for (const [key, ids] of Object.entries(preset)) {
    if (ids.length) params.set(key, ids.join(","));
  }
  return params.toString();
}
