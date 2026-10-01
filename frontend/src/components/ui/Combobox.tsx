import { Check, ChevronDown, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { cn } from "../../lib/cn";
import { inputClasses } from "./Field";
import { InlineError } from "./InlineError";

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
  required = false,
  error,
  helper,
}: {
  label: string;
  options: ComboboxOption[];
  value: number | null;
  onChange: (id: number | null) => void;
  placeholder?: string;
  disabled?: boolean;
  required?: boolean;
  error?: string;
  helper?: string;
}) {
  const inputId = useId();
  const listboxId = useId();
  const errorId = `${inputId}-errore`;
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
    <div className="flex flex-col gap-1.5" ref={containerRef}>
      <label htmlFor={inputId} className="block text-small font-medium text-ink">
        {label}
        {required && (
          <span className="text-danger" aria-hidden>
            {" "}
            *
          </span>
        )}
      </label>
      <div className="relative">
        <input
          id={inputId}
          role="combobox"
          aria-expanded={open}
          aria-controls={listboxId}
          aria-autocomplete="list"
          aria-invalid={!!error}
          aria-describedby={error ? errorId : undefined}
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
            inputClasses,
            "pr-16",
            error &&
              "border-danger enabled:hover:border-danger focus:border-danger focus-visible:outline-danger focus-visible:ring-danger/15",
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
              className="cursor-pointer rounded-mark p-1 text-ink-3 transition-colors hover:text-ink focus-visible:outline-2 focus-visible:outline-accent"
            >
              <X className="size-3.5" aria-hidden />
            </button>
          )}
          <ChevronDown
            className={cn("size-4 text-ink-3 transition-transform", open && "rotate-180")}
            aria-hidden
          />
        </div>

        {open && (
          <ul
            id={listboxId}
            role="listbox"
            aria-label={label}
            className="absolute z-30 mt-1 max-h-56 w-full overflow-y-auto rounded-panel border border-line bg-sheet p-1.5 shadow-overlay"
          >
            {filtered.length === 0 && (
              <li className="px-2.5 py-2 text-body text-ink-3">Nessun risultato</li>
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
                  "flex cursor-pointer items-start gap-2 rounded-md px-2.5 py-2 text-body text-ink",
                  // Voce evidenziata da tastiera: fondo più un anello, non il solo colore.
                  index === highlighted && "bg-sunken outline-2 -outline-offset-2 outline-accent",
                )}
              >
                <Check
                  className={cn(
                    "mt-0.5 size-4 shrink-0",
                    option.id === value ? "text-accent" : "text-transparent",
                  )}
                  aria-hidden
                />
                <span>
                  {option.label}
                  {option.sublabel && (
                    <span className="block text-small text-ink-3">{option.sublabel}</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
      {error ? (
        <InlineError id={errorId}>{error}</InlineError>
      ) : helper ? (
        <p className="text-small text-ink-3">{helper}</p>
      ) : null}
    </div>
  );
}
