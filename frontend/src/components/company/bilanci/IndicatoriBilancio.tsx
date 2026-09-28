import { ChevronDown } from "lucide-react";
import { formatIndicatore, intervalloAnni } from "../../../lib/bilanci";
import { cn } from "../../../lib/cn";
import type { IndicatoreBilancio } from "../../../types";

/** Indicatori calcolati dal server (il frontend non fa conti): valore, anni
 *  usati e formula a richiesta. Un indicatore non calcolabile resta visibile
 *  con il motivo, così si capisce cosa manca. */
export function IndicatoriBilancio({ indicatori }: { indicatori: IndicatoreBilancio[] }) {
  if (indicatori.length === 0) return null;
  return (
    <ul role="list" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {indicatori.map((indicatore) => (
        <li
          key={indicatore.chiave}
          className="flex flex-col rounded-xl border border-slate-200 bg-white p-4 shadow-card"
        >
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            {indicatore.etichetta}
          </p>
          {indicatore.valore !== null ? (
            <>
              <p
                className={cn(
                  "tabular mt-1 font-display text-xl font-bold",
                  indicatore.valore < 0 ? "text-red-600" : "text-slate-900",
                )}
              >
                {formatIndicatore(indicatore)}
              </p>
              {indicatore.anni.length > 0 && (
                <p className="mt-0.5 text-xs text-slate-500">
                  {indicatore.anni.length === 1 ? "Esercizio" : "Esercizi"}{" "}
                  {intervalloAnni(indicatore.anni)}
                </p>
              )}
            </>
          ) : (
            <p className="mt-1 text-sm text-slate-500">
              <span className="font-medium text-slate-700">Dato mancante</span>
              {indicatore.motivo_mancanza ? `: ${indicatore.motivo_mancanza}` : ""}
            </p>
          )}
          {indicatore.formula && (
            <details className="group mt-auto pt-3 text-xs">
              <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded text-slate-500 hover:text-brand-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 [&::-webkit-details-marker]:hidden">
                Come si calcola
                <ChevronDown
                  className="size-3.5 transition-transform group-open:rotate-180"
                  aria-hidden
                />
              </summary>
              <p className="mt-1.5 text-slate-600">{indicatore.formula}</p>
            </details>
          )}
        </li>
      ))}
    </ul>
  );
}
