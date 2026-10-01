import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface ChipProps {
  children: ReactNode;
  /** Se presente, il chip ha un pulsante di rimozione a destra. */
  onRemove?: () => void;
  /** Nome dell'azione di rimozione per lo screen reader (default «Rimuovi»):
   *  conviene dire che cosa si toglie, per esempio «Rimuovi il filtro Regione». */
  label?: string;
  className?: string;
}

/** Filtro attivo (o valore scelto) rimovibile: pillola su `accent-soft` con il
 *  testo in `accent-hover`, 28px. Non è uno stato né un'etichetta: per quelli
 *  ci sono `Status` e `Badge`. */
export function Chip({ children, onRemove, label, className }: ChipProps) {
  return (
    <span
      className={cn(
        "inline-flex h-7 max-w-full items-center gap-1.5 rounded-pill bg-accent-soft pl-2.5 text-small font-medium text-accent-hover",
        onRemove ? "pr-1.5" : "pr-2.5",
        className,
      )}
    >
      <span className="truncate">{children}</span>
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={label ?? "Rimuovi"}
          className={cn(
            "inline-flex size-5 shrink-0 cursor-pointer items-center justify-center rounded-pill",
            "text-accent-hover transition-colors duration-150 ease-uscita hover:bg-accent-line hover:text-ink",
            "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
          )}
        >
          <X className="size-4" aria-hidden />
        </button>
      )}
    </span>
  );
}
