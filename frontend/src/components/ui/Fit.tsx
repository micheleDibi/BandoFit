import { cn } from "../../lib/cn";

export interface FitProps {
  /** Requisiti soddisfatti (viene limitato a 0…totale). */
  soddisfatti: number;
  totale: number;
  /** Testo per lo screen reader; default «Compatibilità: N requisiti su M». */
  label?: string;
  className?: string;
}

/** Compatibilità come contatore di requisiti: barre nel verde del logo e «N su M».
 *  Una compatibilità bassa non è un errore: barre vuote, mai rosso. */
export function Fit({ soddisfatti, totale, label, className }: FitProps) {
  const n = Math.max(0, Math.min(soddisfatti, totale));
  return (
    <span
      role="img"
      aria-label={label ?? `Compatibilità: ${n} requisiti su ${totale}`}
      className={cn(
        "inline-flex items-center gap-2 whitespace-nowrap text-small font-medium text-ink-2",
        className,
      )}
    >
      <span className="inline-flex gap-0.75" aria-hidden>
        {Array.from({ length: totale }, (_, i) => (
          <span
            key={i}
            className={cn(
              "h-4 w-1.5 rounded-xs",
              i < n ? "bg-fit" : "border-[1.5px] border-line-control bg-sheet",
            )}
          />
        ))}
      </span>
      <span className="tabular-nums" aria-hidden>
        {n} su {totale}
      </span>
    </span>
  );
}
