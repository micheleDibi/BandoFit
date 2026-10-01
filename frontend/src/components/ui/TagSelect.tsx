import { Building2, Check, ChevronDown, Plus } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { cn } from "../../lib/cn";
import { inputClasses } from "./Field";

export interface TagSelectOption {
  id: number;
  label: string;
  sublabel?: string;
}

/** Multi-selezione con ricerca (pattern ARIA combobox) per aggiungere valori
 *  a un set: la tendina resta aperta per selezioni multiple, gli elementi
 *  già scelti sono spuntati, quelli EREDITATI dai dati aziendali sono
 *  contrassegnati e non selezionabili (sono sempre inclusi). */
export function TagSelect({
  label,
  options,
  values,
  inherited = [],
  onToggle,
  placeholder = "Cerca e aggiungi…",
}: {
  label: string;
  options: TagSelectOption[];
  values: number[];
  inherited?: number[];
  onToggle: (id: number) => void;
  placeholder?: string;
}) {
  const inputId = useId();
  const listboxId = useId();
  const containerRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [highlighted, setHighlighted] = useState(0);

  const filtered = useMemo(() => {
    if (!search) return options;
    const term = search.toLowerCase();
    return options.filter(
      (option) =>
        option.label.toLowerCase().includes(term) ||
        option.sublabel?.toLowerCase().includes(term),
    );
  }, [options, search]);

  useEffect(() => setHighlighted(0), [search, open]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (!containerRef.current?.contains(e.target as Node)) {
        setOpen(false);
        setSearch("");
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  const toggle = (option: TagSelectOption) => {
    if (inherited.includes(option.id)) return;
    onToggle(option.id);
    // la tendina resta aperta: selezione multipla fluida
    setSearch("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!open && (e.key === "ArrowDown" || e.key === "Enter")) {
      e.preventDefault();
      setOpen(true);
      return;
    }
    if (!open) return;
    if (e.key === "Escape") {
      setOpen(false);
      setSearch("");
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlighted((h) => Math.min(h + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlighted((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (filtered[highlighted]) toggle(filtered[highlighted]);
    }
  };

  return (
    <div className="relative" ref={containerRef}>
      <label htmlFor={inputId} className="sr-only">
        {label}
      </label>
      <div className="relative">
        <Plus
          className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-3"
          aria-hidden
        />
        <input
          id={inputId}
          role="combobox"
          aria-expanded={open}
          aria-controls={listboxId}
          aria-autocomplete="list"
          autoComplete="off"
          value={search}
          placeholder={placeholder}
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            setSearch(e.target.value);
            if (!open) setOpen(true);
          }}
          onKeyDown={handleKeyDown}
          className={cn(inputClasses, "pl-9 pr-9")}
        />
        <ChevronDown
          className={cn(
            "pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-ink-3 transition-transform",
            open && "rotate-180",
          )}
          aria-hidden
        />
      </div>

      {open && (
        <ul
          id={listboxId}
          role="listbox"
          aria-label={label}
          aria-multiselectable="true"
          className="absolute z-30 mt-1 max-h-64 w-full overflow-y-auto rounded-panel border border-line bg-sheet p-1.5 shadow-overlay"
        >
          {filtered.length === 0 && (
            <li className="px-2.5 py-2 text-body text-ink-3">Nessun risultato</li>
          )}
          {filtered.map((option, index) => {
            const isInherited = inherited.includes(option.id);
            const isSelected = values.includes(option.id);
            return (
              <li
                key={option.id}
                role="option"
                aria-selected={isSelected || isInherited}
                aria-disabled={isInherited}
                onPointerDown={(e) => {
                  e.preventDefault();
                  toggle(option);
                }}
                onMouseEnter={() => setHighlighted(index)}
                className={cn(
                  "flex items-start gap-2 rounded-md px-2.5 py-2 text-body",
                  isInherited
                    ? "cursor-default text-ink-3"
                    : cn(
                        "cursor-pointer text-ink",
                        // Voce evidenziata da tastiera: fondo più un anello, non il solo colore.
                        index === highlighted && "bg-sunken outline-2 -outline-offset-2 outline-accent",
                      ),
                )}
              >
                <Check
                  className={cn(
                    "mt-0.5 size-4 shrink-0",
                    isSelected && !isInherited ? "text-accent" : "text-transparent",
                  )}
                  aria-hidden
                />
                <span className="min-w-0 flex-1">
                  {option.label}
                  {option.sublabel && (
                    <span className="block truncate text-small text-ink-3">{option.sublabel}</span>
                  )}
                </span>
                {isInherited && (
                  <span className="inline-flex shrink-0 items-center gap-1 text-caption text-ink-3">
                    <Building2 className="size-3" aria-hidden />
                    dall'azienda
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
