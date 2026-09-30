import { cn } from "../../lib/cn";

export interface StepperProps {
  passi: readonly string[];
  /** Indice (da 0) del passo corrente. */
  corrente: number;
  /** Se presente, i passi già fatti sono cliccabili e riportano al passo `i`. */
  onVai?: (i: number) => void;
  className?: string;
}

/** Passi di un flusso: barrette (fatti e corrente `accent`, futuri `sunken`)
 *  con l'etichetta sotto; il corrente ha `aria-current="step"`. */
export function Stepper({ passi, corrente, onVai, className }: StepperProps) {
  return (
    <ol
      aria-label={`Passo ${corrente + 1} di ${passi.length}`}
      className={cn("flex gap-1", className)}
    >
      {passi.map((passo, i) => {
        const fatto = i < corrente;
        const attuale = i === corrente;
        const contenuto = (
          <>
            <span
              aria-hidden
              className={cn("block h-1 rounded-pill", fatto || attuale ? "bg-accent" : "bg-sunken")}
            />
            <span>{passo}</span>
          </>
        );
        return (
          <li
            key={i}
            aria-current={attuale ? "step" : undefined}
            className={cn(
              "flex min-w-0 flex-1 flex-col text-small font-medium",
              attuale ? "text-ink" : fatto ? "text-ink-2" : "text-ink-3",
            )}
          >
            {fatto && onVai ? (
              <button
                type="button"
                onClick={() => onVai(i)}
                className="flex w-full cursor-pointer flex-col gap-2 rounded-mark text-left hover:text-ink"
              >
                {contenuto}
              </button>
            ) : (
              <div className="flex flex-col gap-2">{contenuto}</div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
