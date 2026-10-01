import { useMemo } from "react";
import { useCompanyFacets } from "../../hooks/useCompany";
import { usePreferences } from "../../hooks/usePreferences";
import { useBandiFilters, type FacetKey } from "../../hooks/useBandiFilters";
import { buildBandiPerTePreset, presetHasValues } from "../../lib/bandiPreset";
import { Segment } from "../ui/Segment";

const sameSet = (a: number[], b: number[]) =>
  a.length === b.length && [...a].sort((x, y) => x - y).join(",") === b.join(",");

/** «nessuna»: i filtri non sono né il preset né «Tutti» (faccette scelte a
 *  mano): nessuna delle due opzioni risulta premuta. */
type Scelta = "tutti" | "per-te" | "nessuna";

const OPZIONI = [
  { id: "tutti", label: "Tutti" },
  { id: "per-te", label: "Adatti alla tua azienda" },
] as const;

/** Le faccette che il preset può impostare e che «Tutti» azzera. */
const FACCETTE_DEL_PRESET: FacetKey[] = [
  "regioni",
  "settori",
  "ateco",
  "beneficiari",
  "tipologie",
  "modalita",
  "programmi",
];

/** Segmento «Tutti / Adatti alla tua azienda» = il preset «Bandi per te»:
 *  applica ai filtri l'unione dei valori REALI dell'azienda e delle
 *  PREFERENZE personali dell'utente. «Tutti» toglie solo le faccette che il
 *  preset imposta, ed è premuto solo quando sono tutte vuote; se i filtri non
 *  coincidono con nessuno dei due, nessuna opzione è premuta (e «Tutti» li
 *  azzera). Senza un preset (profilo vuoto) il segmento non compare. */
export function BandiPerTeSegment({ className }: { className?: string }) {
  const { filters, update } = useBandiFilters();
  const { data: facets } = useCompanyFacets();
  const { data: preferences } = usePreferences();

  const preset = useMemo(
    () => buildBandiPerTePreset(facets, preferences),
    [facets, preferences],
  );

  const hasPreset = presetHasValues(preset);
  const active = useMemo(
    () =>
      hasPreset &&
      (Object.entries(preset) as Array<[FacetKey, number[]]>).every(([key, ids]) =>
        sameSet(filters[key], ids),
      ),
    [filters, preset, hasPreset],
  );

  const tutti = FACCETTE_DEL_PRESET.every((key) => filters[key].length === 0);

  if (!hasPreset) return null;

  const scelta: Scelta = active ? "per-te" : tutti ? "tutti" : "nessuna";

  const handleChange = (id: Scelta) => {
    if (id === "per-te") {
      if (!active) update(preset);
    } else if (id === "tutti" && !tutti) {
      update({
        regioni: [],
        settori: [],
        ateco: [],
        beneficiari: [],
        tipologie: [],
        modalita: [],
        programmi: [],
      });
    }
  };

  return (
    <Segment<Scelta>
      opzioni={OPZIONI}
      valore={scelta}
      onChange={handleChange}
      ariaLabel="Quali bandi mostrare"
      className={className}
    />
  );
}
