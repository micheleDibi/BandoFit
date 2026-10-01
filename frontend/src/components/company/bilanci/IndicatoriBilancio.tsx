import { ChevronDown } from "lucide-react";
import { formatIndicatore, intervalloAnni } from "../../../lib/bilanci";
import type { IndicatoreBilancio } from "../../../types";

/** Indicatori calcolati dal server (il frontend non fa conti): valore, anni
 *  usati e formula a richiesta. Un indicatore non calcolabile resta visibile
 *  con il motivo, così si capisce cosa manca. Un valore negativo è un fatto,
 *  non un errore: stesso colore, il segno basta. */
export function IndicatoriBilancio({ indicatori }: { indicatori: IndicatoreBilancio[] }) {
  if (indicatori.length === 0) return null;
  return (
    <ul role="list" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {indicatori.map((indicatore) => (
        <li
          key={indicatore.chiave}
          className="flex flex-col gap-0.5 rounded-panel border border-line bg-sheet p-4"
        >
          <p className="text-small text-ink-3">{indicatore.etichetta}</p>
          {indicatore.valore !== null ? (
            <>
              <p className="text-figure-sm text-ink">{formatIndicatore(indicatore)}</p>
              {indicatore.anni.length > 0 && (
                <p className="text-small text-ink-3">
                  {indicatore.anni.length === 1 ? "Esercizio" : "Esercizi"}{" "}
                  {intervalloAnni(indicatore.anni)}
                </p>
              )}
            </>
          ) : (
            <p className="text-small text-ink-2">
              <span className="font-medium text-ink">Dato mancante</span>
              {indicatore.motivo_mancanza ? `: ${indicatore.motivo_mancanza}` : ""}
            </p>
          )}
          {indicatore.formula && (
            <details className="group mt-auto pt-3 text-small">
              <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded-mark text-ink-2 hover:text-ink [&::-webkit-details-marker]:hidden">
                Come si calcola
                <ChevronDown className="size-4 group-open:rotate-180" aria-hidden />
              </summary>
              <p className="mt-1.5 text-ink-2">{indicatore.formula}</p>
            </details>
          )}
        </li>
      ))}
    </ul>
  );
}
