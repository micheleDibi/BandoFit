import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  CircleDot,
  HelpCircle,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  XCircle,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useAiChecksForBando } from "../../hooks/useAiCheck";
import { fieldLabel } from "../../lib/aiCheckFields";
import { formatDateTime } from "../../lib/format";
import { scoreColorClasses } from "../../lib/scoreColor";
import type { AiCheck, AiCriterioReport, AiRequisitoReport, AiVerdetto } from "../../types";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";
import { AiEsitoBadge } from "./badges";

const VERDETTO_LABELS: Record<AiVerdetto, string> = {
  soddisfatto: "Soddisfatto",
  parzialmente_soddisfatto: "Parzialmente soddisfatto",
  non_soddisfatto: "Non soddisfatto",
  dato_mancante: "Dato mancante",
};

function VerdettoIcon({ verdetto }: { verdetto: AiVerdetto }) {
  if (verdetto === "soddisfatto") {
    return <CheckCircle2 className="size-4 shrink-0 text-emerald-500" aria-hidden />;
  }
  if (verdetto === "non_soddisfatto") {
    return <XCircle className="size-4 shrink-0 text-red-500" aria-hidden />;
  }
  if (verdetto === "parzialmente_soddisfatto") {
    return <CircleDot className="size-4 shrink-0 text-amber-500" aria-hidden />;
  }
  return <HelpCircle className="size-4 shrink-0 text-amber-500" aria-hidden />;
}

/** Riga espandibile con il dettaglio verificabile del verdetto: citazione del
 *  bando (sezione inclusa) e — per i criteri — il dato aziendale usato. */
function VoceRow({
  voce,
  punti,
  mostraDato = true,
}: {
  voce: AiRequisitoReport | AiCriterioReport;
  punti?: string;
  mostraDato?: boolean;
}) {
  const titolo =
    ("nome" in voce ? voce.nome : undefined) ||
    ("testo" in voce ? voce.testo : undefined) ||
    voce.motivazione;
  return (
    <details className="group rounded-lg border border-slate-200 bg-white">
      <summary className="flex cursor-pointer items-center gap-2.5 px-3.5 py-2.5 text-sm [&::-webkit-details-marker]:hidden">
        <VerdettoIcon verdetto={voce.verdetto} />
        <span className="min-w-0 flex-1 font-medium text-slate-800">{titolo}</span>
        {punti && <span className="tabular shrink-0 text-xs font-semibold text-slate-500">{punti}</span>}
        <span className="sr-only">{VERDETTO_LABELS[voce.verdetto]}.</span>
        <ChevronDown
          className="size-4 shrink-0 text-slate-400 transition-transform group-open:rotate-180"
          aria-hidden
        />
      </summary>
      <div className="space-y-2.5 border-t border-slate-100 px-3.5 py-3 text-sm">
        <p className="text-slate-600">{voce.motivazione}</p>
        <div className="rounded-lg bg-slate-50 px-3 py-2">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
            Dal bando — sezione {voce.riferimento_bando.sezione}
            {!voce.riferimento_bando.verificata && (
              <span className="ml-1.5 normal-case text-amber-600">(citazione non verificata)</span>
            )}
          </p>
          <p className="mt-1 italic text-slate-600">«{voce.riferimento_bando.testo}»</p>
        </div>
        {mostraDato &&
          (voce.dato_azienda ? (
            <p className="text-xs text-slate-500">
              Dato aziendale usato: {fieldLabel(voce.dato_azienda.campo)} —{" "}
              <span className="font-medium text-slate-700">{voce.dato_azienda.valore}</span>
            </p>
          ) : (
            <p className="text-xs text-amber-600">
              Nessun dato aziendale disponibile per questa verifica.
            </p>
          ))}
      </div>
    </details>
  );
}

/** Report completo dell'AI-check, a tutta larghezza sotto la scheda del bando.
 *  Con più analisi in storico è possibile rivedere le versioni precedenti. */
export function AiCheckReport({ slug }: { slug: string }) {
  const { data } = useAiChecksForBando(slug);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const ready = (data?.items ?? []).filter(
    (c): c is AiCheck & { report: NonNullable<AiCheck["report"]> } =>
      c.status === "ready" && c.report !== null,
  );
  if (ready.length === 0) return null;

  const current = ready.find((c) => c.id === selectedId) ?? ready[0];
  const report = current.report;
  const punteggio = report.punteggio_totale;
  const daApprofondire = report.esito_ammissibilita === "non_ammissibile";
  const colori = punteggio !== null ? scoreColorClasses(punteggio) : null;

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

        {/* Esito + punteggio */}
        <div className="mt-5 grid gap-4 sm:grid-cols-[auto_1fr] sm:items-center">
          <AiEsitoBadge esito={report.esito_ammissibilita} />
          <div>
            {punteggio !== null ? (
              <>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-xs font-medium uppercase tracking-wide text-slate-400">
                    Punteggio di compatibilità
                  </span>
                  <span className={`tabular font-display text-xl font-bold ${colori?.text}`}>
                    {punteggio}
                    <span className="text-xs font-medium text-slate-400">/100</span>
                  </span>
                </div>
                <div className="mt-1.5 h-2 rounded-full bg-slate-100">
                  <div
                    className={`h-2 rounded-full ${colori?.bar}`}
                    style={{ width: `${punteggio}%` }}
                  />
                </div>
              </>
            ) : (
              <p className="text-sm text-slate-500">
                Punteggio non calcolabile: il bando non specifica criteri confrontabili.
              </p>
            )}
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {report.tipo_punteggio === "stima" ? (
                <Badge tone="brand">Stima del punteggio ufficiale</Badge>
              ) : (
                <Badge tone="slate">Punteggio euristico interno</Badge>
              )}
              {report.griglia.soglia_minima !== null && report.griglia.punti_ottenuti_stimati !== null && (
                <span className="text-xs text-slate-500">
                  {report.griglia.punti_ottenuti_stimati} punti stimati su soglia minima{" "}
                  {report.griglia.soglia_minima}
                </span>
              )}
            </div>
            {daApprofondire && (
              <p className="mt-1.5 text-xs text-slate-500">
                L'analisi segnala alcuni requisiti da approfondire: guarda i dettagli qui
                sotto e verifica sempre il testo ufficiale del bando.
              </p>
            )}
            {report.esito_ammissibilita === "da_verificare" && (
              <p className="mt-1.5 text-xs font-medium text-amber-600">
                Esito provvisorio: completa i dati indicati sotto per una verifica piena.
              </p>
            )}
          </div>
        </div>

        {/* Requisiti obbligatori */}
        {report.requisiti.length > 0 && (
          <div className="mt-7">
            <h3 className="font-display text-sm font-semibold text-slate-900">
              Requisiti di ammissibilità ({report.requisiti.length})
            </h3>
            <p className="mt-0.5 text-xs text-slate-500">
              La verifica punto per punto dei requisiti obbligatori del bando: apri ogni
              voce per vedere il passaggio citato e il dato aziendale usato.
            </p>
            <div className="mt-3 space-y-2">
              {report.requisiti.map((r) => (
                <VoceRow key={r.id} voce={r} mostraDato={false} />
              ))}
            </div>
          </div>
        )}

        {/* Criteri di valutazione */}
        {report.criteri.length > 0 && (
          <div className="mt-7">
            <h3 className="font-display text-sm font-semibold text-slate-900">
              Criteri di valutazione ({report.criteri.length})
            </h3>
            <div className="mt-3 space-y-2">
              {report.criteri.map((c) => (
                <VoceRow
                  key={c.id}
                  voce={c}
                  punti={
                    c.punti_max !== null && c.punteggio_parziale !== null
                      ? `${c.punteggio_parziale}/${c.punti_max} punti`
                      : undefined
                  }
                />
              ))}
            </div>
          </div>
        )}

        {/* Punti di forza / debolezza */}
        {(report.punti_di_forza.length > 0 || report.punti_di_debolezza.length > 0) && (
          <div className="mt-7 grid gap-4 sm:grid-cols-2">
            {report.punti_di_forza.length > 0 && (
              <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-4">
                <h3 className="inline-flex items-center gap-1.5 font-display text-sm font-semibold text-emerald-800">
                  <ThumbsUp className="size-4" aria-hidden />
                  Punti di forza
                </h3>
                <ul className="mt-2 space-y-1.5 text-sm text-emerald-900">
                  {report.punti_di_forza.map((p, i) => (
                    <li key={i}>• {p.testo}</li>
                  ))}
                </ul>
              </div>
            )}
            {report.punti_di_debolezza.length > 0 && (
              <div className="rounded-xl border border-red-200 bg-red-50/60 p-4">
                <h3 className="inline-flex items-center gap-1.5 font-display text-sm font-semibold text-red-800">
                  <ThumbsDown className="size-4" aria-hidden />
                  Punti di debolezza
                </h3>
                <ul className="mt-2 space-y-1.5 text-sm text-red-900">
                  {report.punti_di_debolezza.map((p, i) => (
                    <li key={i}>• {p.testo}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {/* Dati mancanti */}
        {report.dati_mancanti.length > 0 && (
          <div className="mt-6 rounded-xl border border-amber-200 bg-amber-50 p-4">
            <h3 className="inline-flex items-center gap-1.5 font-display text-sm font-semibold text-amber-800">
              <AlertTriangle className="size-4" aria-hidden />
              Dati mancanti per completare la verifica
            </h3>
            <ul className="mt-2 space-y-1.5 text-sm text-amber-900">
              {report.dati_mancanti.map((d, i) => (
                <li key={i}>
                  • {d.campo ? <span className="font-semibold">{fieldLabel(d.campo)}:</span> : null}{" "}
                  {d.descrizione}
                </li>
              ))}
            </ul>
            <div className="mt-2.5 flex flex-wrap gap-x-5 gap-y-1">
              <Link
                to="/app/profilo"
                className="text-sm font-medium text-amber-800 underline underline-offset-2"
              >
                Completa i dati aziendali →
              </Link>
              <Link
                to="/app/azienda"
                className="text-sm font-medium text-amber-800 underline underline-offset-2"
              >
                Importa dal Registro Imprese →
              </Link>
            </div>
          </div>
        )}

        <p className="mt-6 border-t border-slate-100 pt-4 text-xs text-slate-400">
          {report.disclaimer}
        </p>
      </Card>
    </section>
  );
}
