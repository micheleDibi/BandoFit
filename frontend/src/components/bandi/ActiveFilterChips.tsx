import { X } from "lucide-react";
import type { BandiFilterState, FacetKey } from "../../hooks/useBandiFilters";
import { formatEur } from "../../lib/format";
import type { Lookups } from "../../types";

interface Chip {
  key: string;
  label: string;
  onRemove: () => void;
}

const FACET_LOOKUP: Array<{ facet: FacetKey; lookup: keyof Lookups; prefix?: string }> = [
  { facet: "tipologie", lookup: "tipologie_bando" },
  { facet: "regioni", lookup: "regioni" },
  { facet: "settori", lookup: "settori" },
  { facet: "beneficiari", lookup: "beneficiari" },
  { facet: "modalita", lookup: "modalita_erogazione" },
  { facet: "programmi", lookup: "programmi" },
];

export function ActiveFilterChips({
  filters,
  lookups,
  onToggleFacet,
  onToggleStato,
  onUpdate,
  onReset,
}: {
  filters: BandiFilterState;
  lookups: Lookups | undefined;
  onToggleFacet: (key: FacetKey, id: number) => void;
  onToggleStato: (stato: string) => void;
  onUpdate: (changes: Partial<BandiFilterState>) => void;
  onReset: () => void;
}) {
  if (!lookups) return null;

  const chips: Chip[] = [];

  if (filters.q) {
    chips.push({ key: "q", label: `“${filters.q}”`, onRemove: () => onUpdate({ q: "" }) });
  }
  for (const stato of filters.stato) {
    chips.push({
      key: `stato-${stato}`,
      label: stato === "in apertura prossimamente" ? "In apertura" : stato[0].toUpperCase() + stato.slice(1),
      onRemove: () => onToggleStato(stato),
    });
  }
  for (const { facet, lookup } of FACET_LOOKUP) {
    for (const id of filters[facet]) {
      const item = lookups[lookup].find((x) => x.id === id);
      if (item && "nome" in item) {
        chips.push({
          key: `${facet}-${id}`,
          label: item.nome,
          onRemove: () => onToggleFacet(facet, id),
        });
      }
    }
  }
  for (const id of filters.ateco) {
    const item = lookups.codici_ateco.find((x) => x.id === id);
    if (item) {
      chips.push({
        key: `ateco-${id}`,
        label: `ATECO ${item.codice}`,
        onRemove: () => onToggleFacet("ateco", id),
      });
    }
  }
  if (filters.importo_min !== null) {
    chips.push({
      key: "importo_min",
      label: `Da ${formatEur(filters.importo_min)}`,
      onRemove: () => onUpdate({ importo_min: null }),
    });
  }
  if (filters.importo_max !== null) {
    chips.push({
      key: "importo_max",
      label: `Fino a ${formatEur(filters.importo_max)}`,
      onRemove: () => onUpdate({ importo_max: null }),
    });
  }
  if (filters.scade_entro_giorni !== null) {
    chips.push({
      key: "scade",
      label: `Scade entro ${filters.scade_entro_giorni} gg`,
      onRemove: () => onUpdate({ scade_entro_giorni: null }),
    });
  }

  if (chips.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-2" role="list" aria-label="Filtri attivi">
      {chips.map((chip) => (
        <span
          key={chip.key}
          role="listitem"
          className="inline-flex items-center gap-1 rounded-full bg-brand-50 py-1 pl-3 pr-1.5 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-200"
        >
          {chip.label}
          <button
            type="button"
            onClick={chip.onRemove}
            aria-label={`Rimuovi filtro ${chip.label}`}
            className="cursor-pointer rounded-full p-0.5 transition-colors hover:bg-brand-100 focus-visible:outline-2 focus-visible:outline-brand-500"
          >
            <X className="size-3" aria-hidden />
          </button>
        </span>
      ))}
      {chips.length > 1 && (
        <button
          type="button"
          onClick={onReset}
          className="cursor-pointer text-xs font-medium text-slate-500 underline-offset-2 transition-colors hover:text-brand-600 hover:underline focus-visible:outline-2 focus-visible:outline-brand-500"
        >
          Azzera tutto
        </button>
      )}
    </div>
  );
}
