import type { FacetKey } from "../hooks/useBandiFilters";
import type { CompanyProfile, Preferences } from "../types";

const union = (...groups: Array<number | number[] | null | undefined>): number[] => {
  const out = new Set<number>();
  for (const group of groups) {
    if (group === null || group === undefined) continue;
    for (const id of Array.isArray(group) ? group : [group]) out.add(id);
  }
  return [...out].sort((a, b) => a - b);
};

/** Preset «Bandi per te»: unione dei valori REALI dell'azienda e delle
 *  preferenze personali dell'utente, nelle chiavi dei filtri URL. */
export function buildBandiPerTePreset(
  company: CompanyProfile | null | undefined,
  preferences: Preferences | null | undefined,
): Record<FacetKey, number[]> {
  return {
    regioni: union(company?.regione_id, preferences?.regioni),
    settori: union(company?.settore_id, preferences?.settori),
    ateco: union(company?.ateco_id, preferences?.codici_ateco),
    // Dichiarate sui dati aziendali: ereditate come regione/settore/ATECO.
    beneficiari: union(company?.beneficiari_ids, preferences?.beneficiari),
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
