import { useMemo, useState } from "react";
import { cn } from "../../lib/cn";
import { Checkbox } from "../ui/Checkbox";
import { SearchInput } from "../ui/SearchInput";

export interface FacetOption {
  id: number;
  label: string;
  sublabel?: string;
}

/** Elenco di caselle di una faccetta (regioni, settori, ATECO…), con la
 *  ricerca quando le voci sono molte. Vive dentro il pannello di un `Filter`
 *  (titolo nascosto: lo dice già il pulsante) o nel cassetto «Filtri»
 *  (titolo visibile). Ogni spunta si applica subito. */
export function FacetGroup({
  title,
  options,
  selected,
  onToggle,
  searchable = false,
  titoloVisibile = true,
  className,
}: {
  title: string;
  options: FacetOption[];
  selected: number[];
  onToggle: (id: number) => void;
  searchable?: boolean;
  titoloVisibile?: boolean;
  className?: string;
}) {
  const [search, setSearch] = useState("");

  const visible = useMemo(() => {
    if (!search) return options;
    const term = search.toLowerCase();
    return options.filter(
      (o) =>
        o.label.toLowerCase().includes(term) || o.sublabel?.toLowerCase().includes(term),
    );
  }, [options, search]);

  return (
    <fieldset className={cn("flex min-w-0 flex-col gap-2", className)}>
      <legend className={cn("text-title-group text-ink", !titoloVisibile && "sr-only")}>
        {title}
        {selected.length > 0 && (
          <span className="sr-only">, {selected.length} scelte</span>
        )}
      </legend>
      {searchable && options.length > 8 && (
        <SearchInput
          value={search}
          onChange={setSearch}
          label={`Cerca in ${title}`}
          placeholder="Cerca…"
          className="h-9"
        />
      )}
      <ul className="flex max-h-64 flex-col gap-1.5 overflow-y-auto pr-1">
        {visible.map((option) => (
          <li key={option.id}>
            <Checkbox
              label={option.label}
              descrizione={option.sublabel}
              checked={selected.includes(option.id)}
              onChange={() => onToggle(option.id)}
            />
          </li>
        ))}
        {visible.length === 0 && (
          <li className="py-1 text-small text-ink-3">Nessun risultato</li>
        )}
      </ul>
    </fieldset>
  );
}
