import type { ReactNode } from "react";
import { CALL_COPY } from "../../lib/copy";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { ProgressRing } from "../ui/ProgressRing";
import { Stepper } from "../ui/Stepper";

/** Passi del wizard sullo `Stepper` del design system, in una card con il
 *  bordo dell'area partenariati e l'anello dell'avanzamento (decorativo: lo
 *  `Stepper` dice già «Passo N di 7»): il passo corrente ha
 *  `aria-current="step"`; sono cliccabili i passi che `abilitato` ammette (in
 *  bozza fino all'ultimo raggiunto, dopo la pubblicazione tutti). */
export function CallStepper({
  passo,
  abilitato,
  onVai,
}: {
  passo: number;
  /** Ultimo passo raggiunto e salvato (`wizard_passo` della call). */
  salvatiFinoA: number;
  abilitato: (n: number) => boolean;
  onVai: (n: number) => void;
}) {
  // L'abilitazione è «fino a un passo»: l'ultimo indice ammesso è il raggiunto.
  const raggiunto = CALL_COPY.passi.reduce(
    (massimo, _nome, i) => (abilitato(i + 1) ? i : massimo),
    passo - 1,
  );
  const totale = CALL_COPY.passi.length;
  return (
    <Card area="partenariati" className="flex items-center gap-5 py-4">
      <span aria-hidden className="hidden shrink-0 sm:flex">
        <ProgressRing
          value={passo}
          max={totale}
          size={56}
          tono="partenariati"
          label={`Passo ${passo} di ${totale}`}
        >
          {`${passo}/${totale}`}
        </ProgressRing>
      </span>
      <Stepper
        passi={CALL_COPY.passi}
        corrente={passo - 1}
        raggiunto={raggiunto}
        onVai={(i) => onVai(i + 1)}
        className="min-w-0 grow"
      />
    </Card>
  );
}

/** Barra in fondo a ogni passo: «Indietro» e il salvataggio del passo (l'unico
 *  pulsante pieno del passo), con l'errore del server in un `Alert`. */
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
    <div className="flex flex-col gap-3 border-t border-line pt-4">
      {errore && <Alert tono="errore">{errore}</Alert>}
      {nota && <div className="text-small text-ink-3">{nota}</div>}
      <div className="flex flex-wrap items-center justify-between gap-2">
        {onIndietro ? (
          <Button variant="secondary" onClick={onIndietro} disabled={inCorso}>
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
