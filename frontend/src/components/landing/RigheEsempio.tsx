import { ACCESSO_COPY } from "../../lib/copy/landing";
import { cn } from "../../lib/cn";
import { formatEur, toLocalIsoDate } from "../../lib/format";
import { Due } from "../ui/Due";
import { Fit } from "../ui/Fit";

/** Data di calendario a `giorni` da `oggi`, in `YYYY-MM-DD` locale. */
function traGiorni(oggi: Date, giorni: number): string {
  return toLocalIsoDate(new Date(oggi.getFullYear(), oggi.getMonth(), oggi.getDate() + giorni));
}

/** Due righe d'esempio del registro dei bandi (pannello dell'accesso, hero della
 *  landing). Solo presentazione: niente link né segnalibro, perché prima
 *  dell'accesso non portano da nessuna parte; la scadenza si calcola da oggi,
 *  così l'esempio non scade mai. La larghezza disponibile cambia molto (da ~320
 *  a ~740px): le colonne si dispongono con le container query (stretto: importo e
 *  compatibilità sotto il titolo; medio: in una colonna a destra; largo: in riga
 *  come la tavola «Accesso»). */
export function RigheEsempio({ className }: { className?: string }) {
  const oggi = new Date();
  return (
    <ul
      aria-label={ACCESSO_COPY.esempiEtichetta}
      className={cn("@container flex flex-col border-t border-line", className)}
    >
      {ACCESSO_COPY.esempi.map((esempio) => (
        <li
          key={esempio.titolo}
          className="flex items-start gap-4 border-b border-line px-2 py-4.5 @xl:gap-6"
        >
          <Due data={traGiorni(oggi, esempio.giorni)} oggi={oggi} />
          <div className="flex min-w-0 flex-1 flex-col gap-2 @xl:flex-row @xl:gap-6">
            <p className="min-w-0 flex-1 text-row-title text-ink">{esempio.titolo}</p>
            <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 @xl:w-33 @xl:shrink-0 @xl:flex-col @xl:flex-nowrap @xl:items-end @xl:gap-2 @2xl:w-auto @2xl:flex-row @2xl:items-start @2xl:gap-6">
              <span className="flex items-baseline gap-1.5 @xl:flex-col @xl:items-end @xl:gap-0.5 @2xl:w-33">
                <span className="text-figure-sm tabular-nums text-ink">
                  {formatEur(esempio.importo)}
                </span>
                <span className="text-small text-ink-3">{esempio.nota}</span>
              </span>
              <Fit
                soddisfatti={esempio.soddisfatti}
                totale={esempio.totale}
                className="@2xl:w-30"
              />
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}
