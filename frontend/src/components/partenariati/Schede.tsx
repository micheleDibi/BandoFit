import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface Scheda<T extends string> {
  id: T;
  etichetta: string;
  /** Numero accanto all'etichetta (anche per i lettori di schermo). */
  conteggio?: number;
}

/** Tab accessibili (pattern ARIA tabs, attivazione automatica): frecce
 *  sinistra/destra, Home e Fine spostano la scheda; solo quella scelta è nel
 *  giro del Tab. Lo stato lo tiene il chiamante (searchParams). */
export function Schede<T extends string>({
  etichetta,
  schede,
  attiva,
  onCambia,
  children,
}: {
  /** Nome del gruppo di schede, per i lettori di schermo. */
  etichetta: string;
  schede: Scheda<T>[];
  attiva: T;
  onCambia: (id: T) => void;
  /** Il contenuto della scheda attiva. */
  children: ReactNode;
}) {
  const base = useId();
  const bottoni = useRef<Array<HTMLButtonElement | null>>([]);
  const idScheda = (id: T) => `${base}-scheda-${id}`;
  const idPannello = (id: T) => `${base}-pannello-${id}`;

  const vai = (indice: number) => {
    const n = schede.length;
    const scelta = schede[((indice % n) + n) % n];
    onCambia(scelta.id);
    bottoni.current[((indice % n) + n) % n]?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>, indice: number) => {
    if (e.key === "ArrowRight") vai(indice + 1);
    else if (e.key === "ArrowLeft") vai(indice - 1);
    else if (e.key === "Home") vai(0);
    else if (e.key === "End") vai(schede.length - 1);
    else return;
    e.preventDefault();
  };

  return (
    <div>
      <div
        role="tablist"
        aria-label={etichetta}
        className="flex gap-1 overflow-x-auto border-b border-slate-200"
      >
        {schede.map((s, i) => {
          const selezionata = s.id === attiva;
          return (
            <button
              key={s.id}
              ref={(el) => {
                bottoni.current[i] = el;
              }}
              type="button"
              role="tab"
              id={idScheda(s.id)}
              aria-selected={selezionata}
              aria-controls={idPannello(s.id)}
              tabIndex={selezionata ? 0 : -1}
              onClick={() => onCambia(s.id)}
              onKeyDown={(e) => onKeyDown(e, i)}
              className={cn(
                "-mb-px cursor-pointer whitespace-nowrap border-b-2 px-4 py-2.5 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-brand-500",
                selezionata
                  ? "border-brand-500 text-brand-700"
                  : "border-transparent text-slate-500 hover:text-slate-800",
              )}
            >
              {s.etichetta}
              {s.conteggio !== undefined && (
                <span className="ml-1.5 inline-flex min-w-5 items-center justify-center rounded-full bg-slate-100 px-1.5 text-xs font-semibold text-slate-600 tabular">
                  {s.conteggio}
                </span>
              )}
            </button>
          );
        })}
      </div>
      <div
        role="tabpanel"
        id={idPannello(attiva)}
        aria-labelledby={idScheda(attiva)}
        tabIndex={0}
        className="mt-5 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-brand-500"
      >
        {children}
      </div>
    </div>
  );
}
