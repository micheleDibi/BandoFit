import type { BandiFilterState, FacetKey } from "../../hooks/useBandiFilters";
import { cn } from "../../lib/cn";
import { PARTENARIATO_COPY } from "../../lib/copy";
import type { Lookups } from "../../types";
import { Button } from "../ui/Button";
import { Chip } from "../ui/Chip";
import { STATI, valoreImporto } from "./FiltriBandi";

interface ChipAttivo {
  key: string;
  label: string;
  onRemove: () => void;
  /** Filtro che ha un pulsante nella barra (visibile da `lg`): il chip serve
   *  solo sotto `lg`, dove la barra è nascosta. */
  inBarra?: boolean;
}

/** Faccette numeriche con un pulsante nella barra dei filtri (da `lg`). */
const FACET_BARRA: Array<{
  facet: FacetKey;
  lookup: "regioni" | "tipologie_bando" | "beneficiari";
}> = [
  { facet: "regioni", lookup: "regioni" },
  { facet: "tipologie", lookup: "tipologie_bando" },
  { facet: "beneficiari", lookup: "beneficiari" },
];

/** Faccette che stanno solo nel cassetto «Filtri», senza un pulsante nella
 *  barra: per queste il chip c'è sempre, così si vede che cosa è attivo senza
 *  aprire il cassetto. Quelle della barra (stato, regione, tipologia,
 *  beneficiari, importo, scadenza) hanno il chip solo sotto `lg`, dove la
 *  barra è nascosta; la ricerca si vede nel suo campo. */
const FACET_LOOKUP: Array<{ facet: FacetKey; lookup: keyof Lookups }> = [
  { facet: "settori", lookup: "settori" },
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
  /** «Azzera tutto» dopo i chip, solo sotto `lg`: da `lg` c'è «Azzera i filtri»
   *  nella barra. */
  onReset?: () => void;
}) {
  if (!lookups) return null;

  const chips: ChipAttivo[] = [];

  // Prima i filtri della barra, nello stesso ordine dei pulsanti. Lo stato con
  // la sua parola, mai l'id: un valore sconosciuto (il server lo ignora) non ha chip.
  for (const id of filters.stato) {
    const stato = STATI.find((s) => s.id === id);
    if (stato) {
      chips.push({
        key: `stato-${id}`,
        label: stato.label,
        onRemove: () => onToggleStato(id),
        inBarra: true,
      });
    }
  }
  for (const { facet, lookup } of FACET_BARRA) {
    for (const id of filters[facet]) {
      const item = lookups[lookup].find((x) => x.id === id);
      if (item) {
        chips.push({
          key: `${facet}-${id}`,
          label: item.nome,
          onRemove: () => onToggleFacet(facet, id),
          inBarra: true,
        });
      }
    }
  }
  const importo = valoreImporto(filters.importo_min, filters.importo_max);
  if (importo) {
    chips.push({
      key: "importo",
      label: `Importo ${importo}`,
      onRemove: () => onUpdate({ importo_min: null, importo_max: null }),
      inBarra: true,
    });
  }
  if (filters.scade_entro_giorni !== null) {
    chips.push({
      key: "scadenza",
      label: `Scadenza entro ${filters.scade_entro_giorni} giorni`,
      onRemove: () => onUpdate({ scade_entro_giorni: null }),
      inBarra: true,
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
  // Null anche a modulo partenariati spento (lo decide useBandiFilters).
  if (filters.partenariato !== null) {
    chips.push({
      key: "partenariato",
      label: PARTENARIATO_COPY.filtro[filters.partenariato],
      onRemove: () => onUpdate({ partenariato: null }),
    });
  }

  if (chips.length === 0) return null;

  return (
    <ul
      aria-label="Filtri attivi"
      // Da `lg`, con solo filtri della barra, l'elenco non deve occupare spazio vuoto.
      className={cn(
        "flex flex-wrap items-center gap-2",
        chips.every((chip) => chip.inBarra) && "lg:hidden",
      )}
    >
      {chips.map((chip) => (
        <li key={chip.key} className={chip.inBarra ? "lg:hidden" : undefined}>
          <Chip onRemove={chip.onRemove} label={`Rimuovi il filtro ${chip.label}`}>
            {chip.label}
          </Chip>
        </li>
      ))}
      {onReset && (
        <li className="lg:hidden">
          <Button type="button" variant="ghost" size="sm" onClick={onReset}>
            Azzera tutto
          </Button>
        </li>
      )}
    </ul>
  );
}
