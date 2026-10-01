import { cn } from "../../lib/cn";

export interface StepperProps {
  passi: readonly string[];
  /** Indice (da 0) del passo corrente. */
  corrente: number;
  /** Se presente, i passi raggiungibili sono cliccabili e riportano al passo `i`. */
  onVai?: (i: number) => void;
  /** Indice (da 0) dell'ultimo passo raggiungibile con `onVai`; default il
   *  corrente (solo i passi già fatti). In un flusso salvato a passi si può
   *  tornare avanti fino all'ultimo passo raggiunto. */
  raggiunto?: number;
  className?: string;
}

/** Passi di un flusso: barrette (fatti e corrente `accent`, futuri `sunken`)
 *  con l'etichetta sotto; il corrente ha `aria-current="step"`; i passi
 *  cliccabili (fatti o raggiunti) sono pulsanti. */
export function Stepper({ passi, corrente, onVai, raggiunto = corrente, className }: StepperProps) {
  return (
    <ol
      aria-label={`Passo ${corrente + 1} di ${passi.length}`}
      className={cn("flex gap-1", className)}
    >
      {passi.map((passo, i) => {
        const fatto = i < corrente;
        const attuale = i === corrente;
        const cliccabile = !!onVai && !attuale && i <= raggiunto;
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
            {cliccabile ? (
              <button
                type="button"
                onClick={() => onVai?.(i)}
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
