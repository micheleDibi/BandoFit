import { ChevronDown } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cn } from "../../lib/cn";
import { Popover, type PopoverAlign } from "./Popover";

export interface FilterProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  /** Nome del filtro («Regione», «Stato»); il pannello si chiama «Filtro Regione». */
  label: string;
  /** Vero quando il filtro ha una scelta: il pulsante si evidenzia (`aria-pressed`). */
  attivo?: boolean;
  /** Riassunto della scelta, dopo il nome: «Regione: Piemonte», «Stato: 2». */
  valore?: string;
  /** Contenuto del pannello che il filtro apre (un `Popover`): caselle, radio,
   *  un «Applica» che chiama `usePopover().chiudi()`. Senza, è il solo pulsante. */
  children?: ReactNode;
  /** Allineamento del pannello al trigger (default `start`). */
  align?: PopoverAlign;
}

/** Pulsante-filtro della barra degli elenchi (36px, bordo `line-control`;
 *  attivo su `accent-soft` con bordo `accent`). Con `children` apre un `Popover`,
 *  che clona il trigger: per questo il pulsante inoltra `ref` e tutte le props
 *  del `<button>`. */
export const Filter = forwardRef<HTMLButtonElement, FilterProps>(
  ({ label, attivo = false, valore, children, align, className, type = "button", ...props }, ref) => {
    const trigger = (
      <button
        ref={ref}
        type={type}
        aria-pressed={attivo}
        className={cn(
          "inline-flex h-9 shrink-0 cursor-pointer items-center gap-1.5 rounded-control border px-3",
          "text-body font-medium whitespace-nowrap transition-colors",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
          "disabled:cursor-not-allowed disabled:text-ink-3",
          attivo
            ? "border-accent bg-accent-soft text-accent-hover hover:border-accent-hover"
            : "border-line-control bg-sheet text-ink hover:bg-desk",
          className,
        )}
        {...props}
      >
        <span>{valore ? `${label}: ${valore}` : label}</span>
        <ChevronDown className="size-4" aria-hidden />
      </button>
    );
    if (children === undefined || children === null || children === false) return trigger;
    return (
      <Popover trigger={trigger} align={align} label={`Filtro ${label}`} className="p-3">
        {children}
      </Popover>
    );
  },
);
Filter.displayName = "Filter";
