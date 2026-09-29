import { AlertTriangle, Info } from "lucide-react";
import { CALL_COPY } from "../../lib/copy";
import type { RilievoCall } from "../../types";
import { descriviRilievo } from "./callDati";

/** Rilievi anti-contatti sui testi pubblici, con campo e tipo: i bloccanti
 *  impediscono salvataggio e pubblicazione; gli altri sono avvisi da valutare.
 *  L'estratto (il testo trovato) lo vede solo il creatore. */
export function RilieviCall({ rilievi, titolo }: { rilievi: RilievoCall[]; titolo?: string }) {
  if (rilievi.length === 0) return null;
  const bloccanti = rilievi.filter((r) => r.bloccante);
  const avvisi = rilievi.filter((r) => !r.bloccante);
  return (
    <div className="space-y-2">
      {bloccanti.length > 0 && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">
          <p className="inline-flex items-center gap-2 font-medium">
            <AlertTriangle className="size-4 shrink-0" aria-hidden />
            {titolo ?? "Da togliere prima di pubblicare"}
          </p>
          <ul className="mt-1.5 list-disc space-y-0.5 pl-5">
            {bloccanti.map((r, i) => (
              <li key={`${r.campo}-${r.tipo}-${i}`}>
                {descriviRilievo(r)}
                {r.estratto && <span className="text-red-700"> («{r.estratto}»)</span>}
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-xs">{CALL_COPY.rilieviNota}</p>
        </div>
      )}
      {avvisi.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <p className="inline-flex items-center gap-2 font-medium">
            <Info className="size-4 shrink-0" aria-hidden />
            Potrebbero far riconoscere l'azienda
          </p>
          <ul className="mt-1.5 list-disc space-y-0.5 pl-5">
            {avvisi.map((r, i) => (
              <li key={`${r.campo}-${r.tipo}-${i}`}>
                {descriviRilievo(r)}
                {r.estratto && <span> («{r.estratto}»)</span>}
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-xs">Non bloccano la pubblicazione: valuta tu se toglierli.</p>
        </div>
      )}
    </div>
  );
}
