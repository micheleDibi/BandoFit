import { CheckCircle2, Circle, CircleDashed, Clock, MinusCircle, StickyNote } from "lucide-react";
import { useId, useState } from "react";
import { useStatoDocumento } from "../../hooks/useConsorzio";
import { usePartenariatiVocabolario } from "../../hooks/usePartenariatiVocabolario";
import { apiErrorMessage } from "../../lib/api";
import { CONSORZIO_COPY } from "../../lib/copy";
import { formatDate } from "../../lib/format";
import type {
  DocumentoConsorzio,
  FaseDocumentoConsorzio,
  FormaAggregazione,
  StatoDocumentoConsorzio,
} from "../../types";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { etichettaForma } from "./PartenariatoRegole";

/** Note di un documento (come il server). */
export const NOTE_DOCUMENTO_MAX = 500;

const FASI: readonly FaseDocumentoConsorzio[] = [
  "accordo_preliminare",
  "domanda",
  "concessione",
  "prima_erogazione",
];
const STATI: readonly StatoDocumentoConsorzio[] = ["da_fare", "in_corso", "fatto", "non_applicabile"];

const ICONE: Record<StatoDocumentoConsorzio, { icona: typeof Circle; tono: "slate" | "amber" | "emerald" }> = {
  da_fare: { icona: CircleDashed, tono: "slate" },
  in_corso: { icona: Clock, tono: "amber" },
  fatto: { icona: CheckCircle2, tono: "emerald" },
  non_applicabile: { icona: MinusCircle, tono: "slate" },
};

function StatoDocumentoBadge({ stato }: { stato: StatoDocumentoConsorzio }) {
  const { icona: Icona, tono } = ICONE[stato] ?? ICONE.da_fare;
  return (
    <Badge tone={tono}>
      <Icona className="size-3.5" aria-hidden />
      <span className="sr-only">Stato: </span>
      {CONSORZIO_COPY.statiDocumento[stato] ?? stato}
    </Badge>
  );
}

function RigaDocumento({
  callId,
  documento,
  modificabile,
  onAnnuncio,
}: {
  callId: string;
  documento: DocumentoConsorzio;
  modificabile: boolean;
  onAnnuncio: (testo: string) => void;
}) {
  const id = useId();
  const salva = useStatoDocumento(callId);
  const [nota, setNota] = useState<string | null>(null); // null = editor chiuso
  const scrivi = (stato: StatoDocumentoConsorzio, note: string | null, annuncio: string) =>
    salva.mutate(
      { codice: documento.codice, dati: { stato, note } },
      {
        onSuccess: () => {
          setNota(null);
          onAnnuncio(annuncio);
        },
      },
    );

  return (
    <li className="rounded-lg border border-slate-200 px-3.5 py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <h4 className="text-sm font-medium text-slate-900">{documento.titolo}</h4>
            {documento.obbligatorio ? (
              <Badge tone="amber">Obbligatorio</Badge>
            ) : (
              <Badge tone="slate">Consigliato</Badge>
            )}
            {documento.fonte === "bando" && <Badge tone="brand">Lo chiede il bando</Badge>}
          </div>
          {documento.nota && <p className="mt-1 text-xs text-slate-500">{documento.nota}</p>}
          {documento.note && nota === null && (
            <p className="mt-1.5 flex items-start gap-1.5 text-xs text-slate-600">
              <StickyNote className="mt-0.5 size-3.5 shrink-0 text-slate-400" aria-hidden />
              <span className="whitespace-pre-line">
                <span className="sr-only">Nota: </span>
                {documento.note}
              </span>
            </p>
          )}
          {documento.updated_at && documento.stato !== "da_fare" && (
            <p className="mt-1 text-xs text-slate-400">Aggiornato il {formatDate(documento.updated_at)}</p>
          )}
        </div>
        {modificabile ? (
          <div className="shrink-0">
            <label htmlFor={`${id}-stato`} className="sr-only">
              Stato di «{documento.titolo}»
            </label>
            <select
              id={`${id}-stato`}
              value={documento.stato}
              disabled={salva.isPending}
              onChange={(e) => {
                const stato = e.target.value as StatoDocumentoConsorzio;
                scrivi(
                  stato,
                  documento.note,
                  `«${documento.titolo}»: ${CONSORZIO_COPY.statiDocumento[stato].toLowerCase()}.`,
                );
              }}
              className="h-9 cursor-pointer rounded-lg border border-slate-300 bg-white px-2.5 text-sm text-slate-900 focus:border-brand-500 focus:outline-2 focus:outline-offset-0 focus:outline-brand-500/30 disabled:cursor-not-allowed disabled:bg-slate-50"
            >
              {STATI.map((s) => (
                <option key={s} value={s}>
                  {CONSORZIO_COPY.statiDocumento[s]}
                </option>
              ))}
            </select>
          </div>
        ) : (
          <StatoDocumentoBadge stato={documento.stato} />
        )}
      </div>

      {modificabile && nota === null && (
        <Button
          size="sm"
          variant="ghost"
          className="mt-1.5 -ml-2"
          onClick={() => setNota(documento.note ?? "")}
          aria-label={`${documento.note ? "Modifica la nota" : "Aggiungi una nota"} a «${documento.titolo}»`}
        >
          <StickyNote className="size-4" aria-hidden />
          {documento.note ? "Modifica la nota" : "Aggiungi una nota"}
        </Button>
      )}
      {modificabile && nota !== null && (
        <form
          noValidate
          className="mt-2 space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            scrivi(documento.stato, nota.trim() || null, `Nota salvata su «${documento.titolo}».`);
          }}
        >
          <label htmlFor={`${id}-nota`} className="block text-sm font-medium text-slate-700">
            Nota su «{documento.titolo}»
          </label>
          <textarea
            id={`${id}-nota`}
            rows={3}
            maxLength={NOTE_DOCUMENTO_MAX}
            value={nota}
            onChange={(e) => setNota(e.target.value)}
            aria-describedby={`${id}-contatore`}
            className="w-full resize-y rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm leading-relaxed text-slate-900 focus:border-brand-500 focus:outline-2 focus:outline-offset-0 focus:outline-brand-500/30"
          />
          <p id={`${id}-contatore`} className="text-right text-xs text-slate-400 tabular">
            {nota.length.toLocaleString("it-IT")} caratteri su {NOTE_DOCUMENTO_MAX}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" size="sm" loading={salva.isPending}>
              Salva la nota
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setNota(null)} disabled={salva.isPending}>
              Annulla
            </Button>
          </div>
        </form>
      )}
      {salva.isError && (
        <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {apiErrorMessage(salva.error)}
        </p>
      )}
    </li>
  );
}

/** Checklist documentale per forma di aggregazione (V3, appendice A): i
 *  documenti di base, quelli della forma e quelli che il bando chiede,
 *  raggruppati per quando servono, con lo stato di ciascuno. Solo il titolare
 *  dell'azienda che ha creato la call cambia stato e note. */
export function ChecklistDocumenti({
  callId,
  documenti,
  forma,
  modificabile,
  onAnnuncio,
}: {
  callId: string;
  documenti: DocumentoConsorzio[];
  forma: FormaAggregazione | null;
  modificabile: boolean;
  onAnnuncio: (testo: string) => void;
}) {
  const idTitolo = useId();
  const { data: vocabolario } = usePartenariatiVocabolario();
  const pronti = documenti.filter((d) => d.stato === "fatto" || d.stato === "non_applicabile").length;
  const obbligatori = documenti.filter((d) => d.obbligatorio);
  const obbligatoriPronti = obbligatori.filter((d) => d.stato === "fatto").length;

  return (
    <Card className="p-5">
      <section aria-labelledby={idTitolo}>
        <h2 id={idTitolo} className="font-display text-base font-semibold text-slate-900">
          Documenti del consorzio
        </h2>
        <p className="mt-1 text-sm text-slate-600">
          {forma
            ? `I documenti tipici per la forma «${etichettaForma(forma, vocabolario)}» e quelli che chiede il bando.`
            : "La forma di aggregazione non è ancora decisa: qui trovi i documenti che servono a ogni consorzio e quelli che chiede il bando."}{" "}
          È un promemoria: controlla sempre l'elenco ufficiale del bando.
        </p>
        {documenti.length === 0 ? (
          <p className="mt-3 text-sm text-slate-500">Nessun documento in elenco.</p>
        ) : (
          <>
            <p className="mt-2 text-sm text-slate-700">
              Pronti <span className="font-semibold tabular">{pronti}</span> documenti su{" "}
              <span className="tabular">{documenti.length}</span>
              {obbligatori.length > 0 && (
                <>
                  {" "}
                  (obbligatori fatti: <span className="tabular">{obbligatoriPronti}</span> su{" "}
                  <span className="tabular">{obbligatori.length}</span>)
                </>
              )}
            </p>
            <div className="mt-3 space-y-4">
              {FASI.map((fase) => {
                const gruppo = documenti.filter((d) => d.fase === fase);
                if (gruppo.length === 0) return null;
                return (
                  <div key={fase}>
                    <h3 className="text-xs font-medium uppercase tracking-wide text-slate-400">
                      {CONSORZIO_COPY.fasiDocumento[fase]}
                    </h3>
                    <ul className="mt-2 space-y-2">
                      {gruppo.map((d) => (
                        <RigaDocumento
                          key={d.codice}
                          callId={callId}
                          documento={d}
                          modificabile={modificabile}
                          onAnnuncio={onAnnuncio}
                        />
                      ))}
                    </ul>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </section>
    </Card>
  );
}
