import { Sparkles } from "lucide-react";
import { useMemo } from "react";
import { useCompany } from "../../hooks/useCompany";
import { usePreferences } from "../../hooks/usePreferences";
import { useBandiFilters, type FacetKey } from "../../hooks/useBandiFilters";
import { buildBandiPerTePreset, presetHasValues } from "../../lib/bandiPreset";
import { cn } from "../../lib/cn";

const sameSet = (a: number[], b: number[]) =>
  a.length === b.length && [...a].sort((x, y) => x - y).join(",") === b.join(",");

/** Preset «Bandi per te»: applica ai filtri l'unione dei valori REALI
 * dell'azienda e delle PREFERENZE personali dell'utente. */
export function BandiPerTeButton() {
  const { filters, update } = useBandiFilters();
  const { data: companyData } = useCompany();
  const { data: preferences } = usePreferences();

  const preset = useMemo(
    () => buildBandiPerTePreset(companyData?.company, preferences),
    [companyData, preferences],
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

  if (!hasPreset) return null;

  const handleClick = () => {
    if (active) {
      // secondo click: rimuove il preset (solo le faccette che imposta)
      update({
        regioni: [], settori: [], ateco: [], beneficiari: [],
        tipologie: [], modalita: [], programmi: [],
      });
    } else {
      update(preset);
    }
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      aria-pressed={active}
      title="Filtra con i dati della tua azienda e le tue preferenze"
      className={cn(
        "inline-flex h-11 cursor-pointer items-center gap-2 rounded-xl border px-4 text-sm font-medium shadow-card transition-colors focus-visible:outline-2 focus-visible:outline-brand-500",
        active
          ? "border-brand-500 bg-brand-500 text-white hover:bg-brand-600"
          : "border-brand-200 bg-brand-50 text-brand-700 hover:bg-brand-100",
      )}
    >
      <Sparkles className="size-4" aria-hidden />
      Bandi per te
    </button>
  );
}
