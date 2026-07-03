import { Check, ChevronDown, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { cn } from "../../lib/cn";

export interface ComboboxOption {
  id: number;
  label: string;
  sublabel?: string;
}

/** Select con ricerca (pattern ARIA combobox) per liste lunghe:
 *  codici ATECO, settori, regioni. Selezione singola, azzerabile. */
export function Combobox({
  label,
  options,
  value,
  onChange,
  placeholder = "Cerca…",
  disabled = false,
}: {
  label: string;
  options: ComboboxOption[];
  value: number | null;
  onChange: (id: number | null) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  const inputId = useId();
  const listboxId = useId();
  const containerRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [highlighted, setHighlighted] = useState(0);

  const selected = useMemo(
    () => options.find((option) => option.id === value) ?? null,
    [options, value],
  );

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

  // Chiusura al click fuori.
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

  const select = (option: ComboboxOption) => {
    onChange(option.id);
    setOpen(false);
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
      if (filtered[highlighted]) select(filtered[highlighted]);
    }
  };

  return (
    <div className="space-y-1.5" ref={containerRef}>
      <label htmlFor={inputId} className="block text-sm font-medium text-slate-700">
        {label}
      </label>
      <div className="relative">
        <input
          id={inputId}
          role="combobox"
          aria-expanded={open}
          aria-controls={listboxId}
          aria-autocomplete="list"
          autoComplete="off"
          disabled={disabled}
          value={open ? search : (selected?.label ?? "")}
          placeholder={selected ? selected.label : placeholder}
          onFocus={() => !disabled && setOpen(true)}
          onChange={(e) => {
            setSearch(e.target.value);
            if (!open) setOpen(true);
          }}
          onKeyDown={handleKeyDown}
          className={cn(
            "h-10 w-full rounded-lg border border-slate-300 bg-white px-3 pr-16 text-sm text-slate-900",
            "placeholder:text-slate-400 transition-colors duration-150",
            "focus:border-brand-500 focus:outline-2 focus:outline-offset-0 focus:outline-brand-500/30",
            "disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500",
          )}
        />
        <div className="absolute right-2 top-1/2 flex -translate-y-1/2 items-center gap-0.5">
          {selected && !disabled && (
            <button
              type="button"
              aria-label={`Rimuovi ${label}`}
              onClick={() => {
                onChange(null);
                setSearch("");
              }}
              className="cursor-pointer rounded p-1 text-slate-400 transition-colors hover:text-slate-600 focus-visible:outline-2 focus-visible:outline-brand-500"
            >
              <X className="size-3.5" aria-hidden />
            </button>
          )}
          <ChevronDown className={cn("size-4 text-slate-400 transition-transform", open && "rotate-180")} aria-hidden />
        </div>

        {open && (
          <ul
            id={listboxId}
            role="listbox"
            aria-label={label}
            className="absolute z-30 mt-1 max-h-56 w-full overflow-y-auto rounded-lg border border-slate-200 bg-white py-1 shadow-lg"
          >
            {filtered.length === 0 && (
              <li className="px-3 py-2 text-sm text-slate-400">Nessun risultato</li>
            )}
            {filtered.map((option, index) => (
              <li
                key={option.id}
                role="option"
                aria-selected={option.id === value}
                onPointerDown={(e) => {
                  e.preventDefault();
                  select(option);
                }}
                onMouseEnter={() => setHighlighted(index)}
                className={cn(
                  "flex cursor-pointer items-start gap-2 px-3 py-2 text-sm",
                  index === highlighted ? "bg-brand-50 text-brand-800" : "text-slate-700",
                )}
              >
                <Check
                  className={cn(
                    "mt-0.5 size-3.5 shrink-0",
                    option.id === value ? "text-brand-600" : "text-transparent",
                  )}
                  aria-hidden
                />
                <span>
                  {option.label}
                  {option.sublabel && (
                    <span className="block text-xs text-slate-400">{option.sublabel}</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
