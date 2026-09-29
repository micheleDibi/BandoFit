import { ArrowLeft, Check } from "lucide-react";
import type { ReactNode } from "react";
import { CALL_COPY } from "../../lib/copy";
import { cn } from "../../lib/cn";
import { Button } from "../ui/Button";

/** Passi del wizard: il passo corrente ha `aria-current="step"`; i passi già
 *  salvati hanno la spunta (con il testo per i lettori di schermo). */
export function CallStepper({
  passo,
  salvatiFinoA,
  abilitato,
  onVai,
}: {
  passo: number;
  /** Ultimo passo raggiunto e salvato (`wizard_passo` della call). */
  salvatiFinoA: number;
  abilitato: (n: number) => boolean;
  onVai: (n: number) => void;
}) {
  return (
    <nav aria-label="Passi della creazione della call">
      <ol className="flex gap-1.5 overflow-x-auto pb-1">
        {CALL_COPY.passi.map((nome, i) => {
          const n = i + 1;
          const corrente = n === passo;
          const fatto = n < salvatiFinoA && !corrente;
          return (
            <li key={nome} className="shrink-0">
              <button
                type="button"
                onClick={() => onVai(n)}
                disabled={!abilitato(n)}
                aria-current={corrente ? "step" : undefined}
                className={cn(
                  "inline-flex cursor-pointer items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed disabled:opacity-50",
                  corrente
                    ? "bg-brand-500 text-white"
                    : fatto
                      ? "bg-emerald-50 text-emerald-800 ring-1 ring-inset ring-emerald-200 hover:bg-emerald-100"
                      : "bg-white text-slate-600 ring-1 ring-inset ring-slate-200 hover:bg-slate-50",
                )}
              >
                {fatto ? (
                  <Check className="size-3.5" aria-hidden />
                ) : (
                  <span className="tabular" aria-hidden>
                    {n}.
                  </span>
                )}
                <span className="sr-only">Passo {n}: </span>
                {nome}
                {fatto && <span className="sr-only"> (salvato)</span>}
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/** Barra in fondo a ogni passo: «Indietro» e il salvataggio del passo, con
 *  l'errore del server in `role="alert"`. */
export function BarraPasso({
  onIndietro,
  onAvanti,
  etichettaAvanti = "Salva e continua",
  inCorso = false,
  errore,
  disabilitato = false,
  nota,
}: {
  onIndietro?: () => void;
  onAvanti?: () => void;
  etichettaAvanti?: string;
  inCorso?: boolean;
  errore?: string | null;
  disabilitato?: boolean;
  nota?: ReactNode;
}) {
  return (
    <div className="mt-6 space-y-3 border-t border-slate-200 pt-4">
      {errore && (
        <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {errore}
        </p>
      )}
      {nota && <div className="text-sm text-slate-500">{nota}</div>}
      <div className="flex flex-wrap items-center justify-between gap-2">
        {onIndietro ? (
          <Button variant="ghost" onClick={onIndietro} disabled={inCorso}>
            <ArrowLeft className="size-4" aria-hidden />
            Indietro
          </Button>
        ) : (
          <span />
        )}
        {onAvanti && (
          <Button onClick={onAvanti} loading={inCorso} disabled={disabilitato}>
            {etichettaAvanti}
          </Button>
        )}
      </div>
    </div>
  );
}
