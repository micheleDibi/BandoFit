import { Check, TriangleAlert } from "lucide-react";
import { useId, type ReactNode } from "react";
import { jobInCorsoRecente } from "../../hooks/useCallPartenariato";
import { CALL_COPY } from "../../lib/copy";
import type { JobAiCall } from "../../types";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Panel } from "../ui/Panel";
import { Spinner } from "../ui/Spinner";

/** Blocco di un job AI della call (posizioni o testi): descrizione, avvio e
 *  stato in `aria-live` (in corso, pronta, errore). La proposta la mostra il
 *  chiamante sotto (`children`), e non si salva mai da sola. */
export function JobAiStato({
  job,
  descrizione,
  etichettaAvvia,
  etichettaRigenera,
  onAvvia,
  avvioInCorso,
  erroreAvvio,
  disabilitato,
  motivoDisabilitato,
  children,
}: {
  job: JobAiCall<unknown>;
  descrizione: string;
  etichettaAvvia: string;
  etichettaRigenera: string;
  onAvvia: () => void;
  avvioInCorso: boolean;
  /** Messaggio del rifiuto dell'avvio (limite di oggi, job già in corso…). */
  erroreAvvio: string | null;
  disabilitato?: boolean;
  motivoDisabilitato?: string;
  children?: ReactNode;
}) {
  const idMotivo = useId();
  const inCorso = job.stato === "in_corso";
  const recente = jobInCorsoRecente(job);
  const pronta = job.stato === "pronta" && !!job.proposta;

  let stato: ReactNode = null;
  if (inCorso && recente) {
    stato = (
      <span className="inline-flex items-center gap-2 text-ink-2">
        <Spinner size="sm" />
        {CALL_COPY.aiInCorso}
      </span>
    );
  } else if (inCorso) {
    stato = <span className="text-warning-ink">{CALL_COPY.aiLunga}</span>;
  } else if (pronta) {
    stato = (
      <span className="inline-flex items-center gap-2 text-fit-ink">
        <Check className="size-4" aria-hidden />
        La proposta è pronta: guardala qui sotto.
      </span>
    );
  } else if (job.stato === "errore") {
    stato = (
      <span className="inline-flex items-start gap-2 text-danger">
        <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
        <span>
          {CALL_COPY.aiErrore}
          {job.errore ? ` ${job.errore}` : ""}
        </span>
      </span>
    );
  }

  return (
    <Panel>
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1 text-body text-ink-2">
          <p>{descrizione}</p>
          <p className="text-small text-ink-3">{CALL_COPY.aiNota}</p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={onAvvia}
          loading={avvioInCorso}
          disabled={disabilitato || (inCorso && recente)}
          aria-describedby={disabilitato && motivoDisabilitato ? idMotivo : undefined}
        >
          {pronta || job.stato === "errore" ? etichettaRigenera : etichettaAvvia}
        </Button>
      </div>
      {disabilitato && motivoDisabilitato && (
        <p id={idMotivo} className="text-small text-ink-3">
          {motivoDisabilitato}
        </p>
      )}
      {/* Sempre montato: lo stato del job va annunciato quando cambia. */}
      <div role="status" aria-live="polite" className="text-body">
        {stato}
      </div>
      {erroreAvvio && <Alert tono="errore">{erroreAvvio}</Alert>}
      {children}
    </Panel>
  );
}
