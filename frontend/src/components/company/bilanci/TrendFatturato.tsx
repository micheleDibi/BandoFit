import { formatEurCompatto } from "../../../lib/bilanci";
import { cn } from "../../../lib/cn";
import type { EsercizioBilancio } from "../../../types";

/** Fatturato anno per anno, a barre (div: nel progetto non c'è una libreria
 *  di grafici). Per gli screen reader il grafico è un'immagine con i valori
 *  nell'etichetta; le cifre esatte sono comunque nella tabella. Serve almeno
 *  un confronto: con meno di due anni non si mostra. */
export function TrendFatturato({ esercizi }: { esercizi: EsercizioBilancio[] }) {
  const punti = esercizi
    .filter((e) => e.fatturato !== null)
    .sort((a, b) => a.anno - b.anno)
    .map((e) => ({ anno: e.anno, valore: e.fatturato as number }));
  if (punti.length < 2) return null;

  const massimo = Math.max(0, ...punti.map((p) => p.valore));
  const descrizione = `Fatturato per anno: ${punti
    .map((p) => `${p.anno}, ${formatEurCompatto(p.valore)}`)
    .join("; ")}.`;

  return (
    <div role="img" aria-label={descrizione} className="flex items-end gap-1.5 sm:gap-3">
      {punti.map((p, indice) => {
        const ultimo = indice === punti.length - 1;
        // Un valore positivo resta visibile anche se minuscolo rispetto al massimo.
        const altezza = massimo > 0 ? Math.max((p.valore / massimo) * 100, p.valore > 0 ? 2 : 0) : 0;
        return (
          <div key={p.anno} className="flex min-w-0 flex-1 flex-col items-center gap-1">
            {/* Su mobile le etichette di tutte le barre non ci stanno: resta
                quella dell'ultimo anno, le altre sono nella tabella. */}
            <span
              className={cn(
                "whitespace-nowrap text-caption text-ink-2 tabular-nums",
                !ultimo && "hidden sm:block",
              )}
            >
              {formatEurCompatto(p.valore)}
            </span>
            <div className="flex h-28 w-full items-end justify-center">
              <div
                className={cn("w-full max-w-12 rounded-t-mark", ultimo ? "bg-accent" : "bg-accent-soft")}
                style={{ height: `${altezza}%` }}
              />
            </div>
            <span className="text-caption text-ink-3 tabular-nums">{p.anno}</span>
          </div>
        );
      })}
    </div>
  );
}
