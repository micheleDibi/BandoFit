import { CALL_COPY } from "../../lib/copy";
import type { RilievoCall } from "../../types";
import { Alert } from "../ui/Alert";
import { descriviRilievo } from "./callDati";

/** Rilievi anti-contatti sui testi pubblici, con campo e tipo: i bloccanti
 *  impediscono salvataggio e pubblicazione; gli altri sono avvisi da valutare.
 *  L'estratto (il testo trovato) lo vede solo il creatore. */
export function RilieviCall({ rilievi, titolo }: { rilievi: RilievoCall[]; titolo?: string }) {
  if (rilievi.length === 0) return null;
  const bloccanti = rilievi.filter((r) => r.bloccante);
  const avvisi = rilievi.filter((r) => !r.bloccante);
  return (
    <div className="flex flex-col gap-2">
      {bloccanti.length > 0 && (
        <Alert tono="errore" titolo={titolo ?? "Da togliere prima di pubblicare"}>
          <ul className="list-disc pl-5">
            {bloccanti.map((r, i) => (
              <li key={`${r.campo}-${r.tipo}-${i}`}>
                {descriviRilievo(r)}
                {r.estratto && <span className="text-ink-2"> («{r.estratto}»)</span>}
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-small text-ink-3">{CALL_COPY.rilieviNota}</p>
        </Alert>
      )}
      {avvisi.length > 0 && (
        <Alert tono="attenzione" titolo="Potrebbero far riconoscere l'azienda">
          <ul className="list-disc pl-5">
            {avvisi.map((r, i) => (
              <li key={`${r.campo}-${r.tipo}-${i}`}>
                {descriviRilievo(r)}
                {r.estratto && <span> («{r.estratto}»)</span>}
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-small text-ink-3">
            Non bloccano la pubblicazione: valuta tu se toglierli.
          </p>
        </Alert>
      )}
    </div>
  );
}
