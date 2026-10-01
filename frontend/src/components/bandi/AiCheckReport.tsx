import { useState } from "react";
import { useAiChecksForBando } from "../../hooks/useAiCheck";
import { useEntitlements } from "../../hooks/useEntitlements";
import { formatDateTime } from "../../lib/format";
import type { AiCheck } from "../../types";
import { Select } from "../ui/Select";
import { ErrorState, Skeleton } from "../ui/states";
import { AiReportBody } from "./AiReportBody";

/** Report completo dell'AI-check, a tutta larghezza sotto la scheda del bando
 *  (il titolo di sezione con l'ancora `ai-check-report` lo mette la pagina).
 *  Senza report: la frase che dice come averne uno, e l'invito ad avviarlo
 *  solo a chi può (stessa regola della card «Fa per te?»). Con più analisi in
 *  storico si possono rivedere le versioni precedenti. Il corpo del report è
 *  AiReportBody (condiviso con l'area progettista). */
export function AiCheckReport({ slug }: { slug: string }) {
  const { data, isPending, isError, refetch } = useAiChecksForBando(slug);
  const entitlements = useEntitlements();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Può avviarlo il titolare, o un membro attivo entro il suo budget (come in
  // AiCheckCard), e solo se il piano include AI-check.
  const editable = data?.editable ?? false;
  const membroAttivo = !editable && !!entitlements.data && !entitlements.data.editable;
  const pianoConAiCheck = (data?.quota?.totale ?? 0) > 0;
  const puoAvviare = (editable || membroAttivo) && pianoConAiCheck;

  const ready = (data?.items ?? []).filter(
    (c): c is AiCheck & { report: NonNullable<AiCheck["report"]> } =>
      c.status === "ready" && c.report !== null,
  );

  // Prima dei dati (o dopo un errore) non si può dire che il report manca.
  if (isPending) {
    return (
      <div className="flex flex-col gap-3" aria-hidden>
        <Skeleton className="h-4 w-56" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (isError && !data) {
    return (
      <ErrorState
        title="Non siamo riusciti a caricare il report AI-check."
        onRetry={() => void refetch()}
      />
    );
  }

  if (ready.length === 0) {
    return (
      <div className="flex max-w-[520px] flex-col gap-1">
        <p className="text-row-title text-ink">Non hai ancora un report su questo bando.</p>
        {puoAvviare && (
          <p className="text-body text-ink-2">
            Avvia un AI-check per sapere, requisito per requisito, se la tua azienda può
            partecipare e che cosa le manca.
          </p>
        )}
      </div>
    );
  }

  const current = ready.find((c) => c.id === selectedId) ?? ready[0];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <p className="text-small text-ink-2">
          Generato il {formatDateTime(current.ready_at ?? current.created_at)}
          {current.extraction_cached && ", requisiti già estratti in precedenza"}
        </p>
        {ready.length > 1 && (
          <Select
            id="ai-check-version"
            label="Versione del report"
            value={current.id}
            onChange={(e) => setSelectedId(e.target.value)}
            className="h-9"
          >
            {ready.map((c, i) => (
              <option key={c.id} value={c.id}>
                {i === 0 ? "Ultimo AI-check" : `AI-check del ${formatDateTime(c.ready_at ?? c.created_at)}`}
              </option>
            ))}
          </Select>
        )}
      </div>
      <AiReportBody report={current.report} />
    </div>
  );
}
