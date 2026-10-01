import { cn } from "../../lib/cn";
import { ProgressRing } from "./ProgressRing";

export interface FitProps {
  /** Requisiti soddisfatti (viene limitato a 0…totale). */
  soddisfatti: number;
  totale: number;
  /** Testo per lo screen reader; default «Compatibilità: N requisiti su M». */
  label?: string;
  /** `barre` (default): un segmento per requisito. `anello`: un `ProgressRing`
   *  verde con N al centro e «su M» accanto, per i riquadri in evidenza. */
  variante?: "barre" | "anello";
  className?: string;
}

/** Compatibilità come contatore di requisiti: segmenti verdi pieni (il verde
 *  del logo) e «N su M». Una compatibilità bassa non è un errore: segmenti
 *  vuoti, mai rosso. */
export function Fit({ soddisfatti, totale, label, variante = "barre", className }: FitProps) {
  const n = Math.max(0, Math.min(soddisfatti, totale));
  const nome = label ?? `Compatibilità: ${n} requisiti su ${totale}`;

  if (variante === "anello") {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-2 whitespace-nowrap text-small font-medium text-ink-2",
          className,
        )}
      >
        <ProgressRing value={n} max={totale} size={40} tono="fit" label={nome}>
          {n}
        </ProgressRing>
        <span className="tabular-nums" aria-hidden>
          su {totale}
        </span>
      </span>
    );
  }

  return (
    <span
      role="img"
      aria-label={nome}
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
              "h-4 w-2 rounded-xs",
              i < n ? "bg-fit" : "border-[1.5px] border-line-control bg-sheet",
            )}
          />
        ))}
      </span>
      <span className="tabular-nums" aria-hidden>
        <span className="font-semibold text-ink">{n}</span> su {totale}
      </span>
    </span>
  );
}
