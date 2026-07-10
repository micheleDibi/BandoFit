import { Sparkles } from "lucide-react";
import { useState } from "react";
import { useAiChecksForBando } from "../../hooks/useAiCheck";
import { formatDateTime } from "../../lib/format";
import type { AiCheck } from "../../types";
import { Card } from "../ui/Card";
import { AiReportBody } from "./AiReportBody";

/** Report completo dell'AI-check, a tutta larghezza sotto la scheda del bando.
 *  Con più analisi in storico è possibile rivedere le versioni precedenti.
 *  Il corpo del report è AiReportBody (condiviso con l'area progettista). */
export function AiCheckReport({ slug }: { slug: string }) {
  const { data } = useAiChecksForBando(slug);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const ready = (data?.items ?? []).filter(
    (c): c is AiCheck & { report: NonNullable<AiCheck["report"]> } =>
      c.status === "ready" && c.report !== null,
  );
  if (ready.length === 0) return null;

  const current = ready.find((c) => c.id === selectedId) ?? ready[0];

  return (
    <section id="ai-check-report" aria-label="Report AI-check" className="mt-10 scroll-mt-24">
      <Card className="p-6">
        {/* Intestazione */}
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="inline-flex items-center gap-2 font-display text-lg font-bold text-slate-900">
              <Sparkles className="size-5 text-brand-500" aria-hidden />
              Report di compatibilità
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              Generato il {formatDateTime(current.ready_at ?? current.created_at)}
              {current.extraction_cached && " · requisiti già estratti in precedenza"}
            </p>
          </div>
          {ready.length > 1 && (
            <div>
              <label className="sr-only" htmlFor="ai-check-version">
                Versione del report
              </label>
              <select
                id="ai-check-version"
                value={current.id}
                onChange={(e) => setSelectedId(e.target.value)}
                className="h-9 cursor-pointer rounded-lg border border-slate-300 bg-white px-2.5 text-sm focus:border-brand-500 focus:outline-none"
              >
                {ready.map((c, i) => (
                  <option key={c.id} value={c.id}>
                    {i === 0
                      ? "Ultima analisi"
                      : `Analisi del ${formatDateTime(c.ready_at ?? c.created_at)}`}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        <AiReportBody report={current.report} />
      </Card>
    </section>
  );
}
