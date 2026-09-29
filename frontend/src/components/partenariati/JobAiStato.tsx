import { AlertTriangle, CheckCircle2, Loader2, Sparkles } from "lucide-react";
import { useId, type ReactNode } from "react";
import { jobInCorsoRecente } from "../../hooks/useCallPartenariato";
import { CALL_COPY } from "../../lib/copy";
import type { JobAiCall } from "../../types";
import { Button } from "../ui/Button";

/** Riquadro di un job AI della call (posizioni o testi): descrizione, avvio e
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
      <span className="inline-flex items-center gap-1.5 text-brand-800">
        <Loader2 className="size-4 animate-spin" aria-hidden />
        {CALL_COPY.aiInCorso}
      </span>
    );
  } else if (inCorso) {
    stato = <span className="text-amber-800">{CALL_COPY.aiLunga}</span>;
  } else if (pronta) {
    stato = (
      <span className="inline-flex items-center gap-1.5 text-emerald-800">
        <CheckCircle2 className="size-4" aria-hidden />
        La proposta è pronta: guardala qui sotto.
      </span>
    );
  } else if (job.stato === "errore") {
    stato = (
      <span className="inline-flex items-start gap-1.5 text-red-700">
        <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
        <span>
          {CALL_COPY.aiErrore}
          {job.errore ? ` ${job.errore}` : ""}
        </span>
      </span>
    );
  }

  return (
    <div className="rounded-lg border border-dashed border-brand-200 bg-brand-50/40 px-4 py-3">
      <div className="flex flex-wrap items-start gap-3">
        <Sparkles className="mt-0.5 size-4 shrink-0 text-brand-500" aria-hidden />
        <div className="min-w-0 flex-1 text-sm text-slate-700">
          <p>{descrizione}</p>
          <p className="mt-0.5 text-xs text-slate-500">{CALL_COPY.aiNota}</p>
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
        <p id={idMotivo} className="mt-2 text-xs text-slate-500">
          {motivoDisabilitato}
        </p>
      )}
      {/* Sempre montato: lo stato del job va annunciato quando cambia. */}
      <div role="status" aria-live="polite" className="mt-2 text-sm empty:mt-0">
        {stato}
      </div>
      {erroreAvvio && (
        <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {erroreAvvio}
        </p>
      )}
      {children}
    </div>
  );
}
