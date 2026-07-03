import { ChevronDown, Search } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { cn } from "../../lib/cn";

export interface FacetOption {
  id: number;
  label: string;
  sublabel?: string;
}

export function FacetGroup({
  title,
  options,
  selected,
  onToggle,
  searchable = false,
  defaultOpen = false,
}: {
  title: string;
  options: FacetOption[];
  selected: number[];
  onToggle: (id: number) => void;
  searchable?: boolean;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen || selected.length > 0);
  const [search, setSearch] = useState("");
  const panelId = useId();

  const visible = useMemo(() => {
    if (!search) return options;
    const term = search.toLowerCase();
    return options.filter(
      (o) =>
        o.label.toLowerCase().includes(term) || o.sublabel?.toLowerCase().includes(term),
    );
  }, [options, search]);

  return (
    <div className="border-b border-slate-100 py-1 last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={panelId}
        className="flex w-full cursor-pointer items-center justify-between rounded-lg px-2 py-2.5 text-left transition-colors hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-brand-500"
      >
        <span className="text-sm font-semibold text-slate-800">
          {title}
          {selected.length > 0 && (
            <span className="ml-2 rounded-full bg-brand-500 px-1.5 py-0.5 text-xs font-semibold text-white">
              {selected.length}
            </span>
          )}
        </span>
        <ChevronDown
          className={cn("size-4 text-slate-400 transition-transform duration-200", open && "rotate-180")}
          aria-hidden
        />
      </button>

      {open && (
        <div id={panelId} className="px-2 pb-3">
          {searchable && options.length > 8 && (
            <div className="relative mb-2">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-slate-400" aria-hidden />
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={`Cerca in ${title.toLowerCase()}…`}
                aria-label={`Cerca in ${title}`}
                className="h-8 w-full rounded-md border border-slate-200 bg-slate-50 pl-8 pr-2 text-xs focus:border-brand-400 focus:bg-white focus:outline-none"
              />
            </div>
          )}
          <ul className="max-h-52 space-y-0.5 overflow-y-auto pr-1">
            {visible.map((option) => {
              const checked = selected.includes(option.id);
              return (
                <li key={option.id}>
                  <label className="flex cursor-pointer items-start gap-2 rounded-md px-1.5 py-1.5 text-sm transition-colors hover:bg-slate-50">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => onToggle(option.id)}
                      className="mt-0.5 size-4 shrink-0 cursor-pointer accent-brand-500"
                    />
                    <span className={cn("leading-snug", checked ? "font-medium text-slate-900" : "text-slate-600")}>
                      {option.label}
                      {option.sublabel && (
                        <span className="block text-xs text-slate-400">{option.sublabel}</span>
                      )}
                    </span>
                  </label>
                </li>
              );
            })}
            {visible.length === 0 && (
              <li className="px-1.5 py-2 text-xs text-slate-400">Nessun risultato</li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
