import { cn } from "../../lib/cn";

export interface SegmentOpzione<T extends string = string> {
  id: T;
  label: string;
}

/** Generico sull'unione degli id: `onChange` accetta un setter tipizzato senza cast. */
export interface SegmentProps<T extends string = string> {
  /** Due o tre scelte; oltre, meglio `Tabs` o un `Select`. */
  opzioni: readonly SegmentOpzione<T>[];
  /** `id` dell'opzione scelta. */
  valore: T;
  onChange: (id: T) => void;
  /** Nome del gruppo per lo screen reader («Quali bandi mostrare»). */
  ariaLabel: string;
  className?: string;
}

/** Segmento a due o tre scelte (es. «Tutti / Adatti alla tua azienda»): pista
 *  su `sunken`, scelta corrente su `sheet` con l'ombra `card` e il testo in
 *  `accent`. Ogni scelta è un pulsante con `aria-pressed`. */
export function Segment<T extends string>({
  opzioni,
  valore,
  onChange,
  ariaLabel,
  className,
}: SegmentProps<T>) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className={cn("inline-flex shrink-0 rounded-control bg-sunken p-0.5", className)}
    >
      {opzioni.map((opzione) => {
        const attiva = opzione.id === valore;
        return (
          <button
            key={opzione.id}
            type="button"
            aria-pressed={attiva}
            onClick={() => onChange(opzione.id)}
            className={cn(
              "h-8 cursor-pointer rounded-md px-3 text-body font-medium whitespace-nowrap transition duration-150 ease-uscita",
              "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
              attiva ? "bg-sheet text-accent-hover shadow-card" : "text-ink-2 hover:text-ink",
            )}
          >
            {opzione.label}
          </button>
        );
      })}
    </div>
  );
}
