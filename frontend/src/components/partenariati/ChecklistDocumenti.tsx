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
import { Alert } from "../ui/Alert";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { TextareaField } from "../ui/Field";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { Select } from "../ui/Select";
import { Status, type TonoStatus } from "../ui/Status";
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

const TONI: Record<StatoDocumentoConsorzio, TonoStatus> = {
  da_fare: "neutro",
  in_corso: "in-apertura",
  fatto: "aperto",
  non_applicabile: "chiuso",
};

function StatoDocumentoBadge({ stato }: { stato: StatoDocumentoConsorzio }) {
  return (
    <Status tono={TONI[stato] ?? "neutro"}>
      <span className="sr-only">Stato: </span>
      {CONSORZIO_COPY.statiDocumento[stato] ?? stato}
    </Status>
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
    <li className="flex flex-col gap-2 border-b border-line py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <h4 className="font-sans text-title-group text-ink">{documento.titolo}</h4>
            <Badge>{documento.obbligatorio ? "Obbligatorio" : "Consigliato"}</Badge>
            {documento.fonte === "bando" && <Badge>Lo chiede il bando</Badge>}
          </div>
          {documento.nota && <p className="text-small text-ink-3">{documento.nota}</p>}
          {documento.note && nota === null && (
            <p className="whitespace-pre-line text-small text-ink-2">
              <span className="font-medium text-ink">Nota: </span>
              {documento.note}
            </p>
          )}
          {documento.updated_at && documento.stato !== "da_fare" && (
            <p className="text-small text-ink-3">Aggiornato il {formatDate(documento.updated_at)}</p>
          )}
        </div>
        {modificabile ? (
          <Select
            label={`Stato di «${documento.titolo}»`}
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
          >
            {STATI.map((s) => (
              <option key={s} value={s}>
                {CONSORZIO_COPY.statiDocumento[s]}
              </option>
            ))}
          </Select>
        ) : (
          <StatoDocumentoBadge stato={documento.stato} />
        )}
      </div>

      {modificabile && nota === null && (
        <div>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setNota(documento.note ?? "")}
            aria-label={`${documento.note ? "Modifica la nota" : "Aggiungi una nota"} a «${documento.titolo}»`}
          >
            {documento.note ? "Modifica la nota" : "Aggiungi una nota"}
          </Button>
        </div>
      )}
      {modificabile && nota !== null && (
        <form
          noValidate
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            scrivi(documento.stato, nota.trim() || null, `Nota salvata su «${documento.titolo}».`);
          }}
        >
          <TextareaField
            label={`Nota su «${documento.titolo}»`}
            rows={3}
            maxLength={NOTE_DOCUMENTO_MAX}
            value={nota}
            onChange={(e) => setNota(e.target.value)}
            aria-describedby={`${id}-contatore`}
          />
          <p id={`${id}-contatore`} className="text-right text-small text-ink-3 tabular-nums">
            {nota.length.toLocaleString("it-IT")} caratteri su {NOTE_DOCUMENTO_MAX}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" size="sm" variant="secondary" loading={salva.isPending}>
              Salva la nota
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setNota(null)} disabled={salva.isPending}>
              Annulla
            </Button>
          </div>
        </form>
      )}
      {salva.isError && <Alert tono="errore">{apiErrorMessage(salva.error)}</Alert>}
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
  const { data: vocabolario } = usePartenariatiVocabolario();
  const pronti = documenti.filter((d) => d.stato === "fatto" || d.stato === "non_applicabile").length;
  const obbligatori = documenti.filter((d) => d.obbligatorio);
  const obbligatoriPronti = obbligatori.filter((d) => d.stato === "fatto").length;

  return (
    <Section>
      <SectionHeader titolo="Documenti del consorzio" />
      <p className="text-body text-ink-2">
        {forma
          ? `I documenti tipici per la forma «${etichettaForma(forma, vocabolario)}» e quelli che chiede il bando.`
          : "La forma di aggregazione non è ancora decisa: qui trovi i documenti che servono a ogni consorzio e quelli che chiede il bando."}{" "}
        È un promemoria: controlla sempre l'elenco ufficiale del bando.
      </p>
      {documenti.length === 0 ? (
        <p className="text-body text-ink-3">Nessun documento in elenco.</p>
      ) : (
        <>
          <p className="text-body text-ink tabular-nums">
            Pronti <span className="font-semibold">{pronti}</span> documenti su {documenti.length}
            {obbligatori.length > 0 && (
              <>
                {" "}
                (obbligatori fatti: {obbligatoriPronti} su {obbligatori.length})
              </>
            )}
          </p>
          <div className="flex flex-col gap-6">
            {FASI.map((fase) => {
              const gruppo = documenti.filter((d) => d.fase === fase);
              if (gruppo.length === 0) return null;
              return (
                <div key={fase} className="flex flex-col gap-1">
                  <h3 className="font-sans text-small font-medium text-ink-3">
                    {CONSORZIO_COPY.fasiDocumento[fase]}
                  </h3>
                  <ul className="flex flex-col border-t border-line">
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
    </Section>
  );
}
