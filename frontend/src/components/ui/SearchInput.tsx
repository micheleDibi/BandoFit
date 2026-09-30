import { Search } from "lucide-react";
import { forwardRef, useId, type InputHTMLAttributes } from "react";
import { cn } from "../../lib/cn";
import { inputClasses } from "./Field";

export interface SearchInputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type"> {
  value: string;
  /** Riceve direttamente il testo digitato. */
  onChange: (valore: string) => void;
  placeholder?: string;
  /** Etichetta per lo screen reader, visivamente nascosta («Cerca nei bandi»). */
  label: string;
}

/** Campo di ricerca delle barre degli elenchi: icona `Search` e le classi
 *  comuni dei controlli (`inputClasses`) sul contenitore; bordo e anello del
 *  focus seguono il contenitore (`focus-within`), non l'input. */
export const SearchInput = forwardRef<HTMLInputElement, SearchInputProps>(
  ({ value, onChange, placeholder, label, id, className, disabled, ...props }, ref) => {
    const autoId = useId();
    const inputId = id ?? autoId;
    return (
      <div
        className={cn(
          inputClasses,
          "flex items-center gap-2 text-ink-3",
          "focus-within:border-accent focus-within:outline-2 focus-within:outline-offset-0 focus-within:outline-accent",
          disabled && "cursor-not-allowed bg-sunken",
          className,
        )}
      >
        <label htmlFor={inputId} className="sr-only">
          {label}
        </label>
        <Search className="size-4 shrink-0" aria-hidden />
        <input
          ref={ref}
          id={inputId}
          type="search"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          className={cn(
            "min-w-0 flex-1 bg-transparent text-body text-ink outline-none placeholder:text-ink-3",
            "disabled:cursor-not-allowed disabled:text-ink-3",
            "[&::-webkit-search-cancel-button]:appearance-none",
          )}
          {...props}
        />
      </div>
    );
  },
);
SearchInput.displayName = "SearchInput";
