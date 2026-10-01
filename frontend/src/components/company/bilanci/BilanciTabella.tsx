import { useEffect, useId, useRef } from "react";
import {
  FONTI_BILANCIO,
  GRUPPI_VOCI_BILANCIO,
  ORDINE_FONTI,
  TIPO_BILANCIO_LABELS,
  chiusuraNonSolare,
  formatValoreBilancio,
  intervalloAnni,
  type VoceBilancio,
} from "../../../lib/bilanci";
import { cn } from "../../../lib/cn";
import { formatDateNumeric } from "../../../lib/format";
import type { EsercizioBilancio, FonteBilancio } from "../../../types";

/** Classi della prima colonna: resta ferma mentre gli anni scorrono in
 *  orizzontale, quindi deve avere uno sfondo pieno. */
const PRIMA_COLONNA =
  "sticky left-0 z-10 min-w-38 max-w-56 border-r border-line px-4 text-left";

function Cella({
  esercizio,
  voce,
  marcaFonte,
}: {
  esercizio: EsercizioBilancio;
  voce: VoceBilancio;
  marcaFonte: boolean;
}) {
  const valore = esercizio[voce.campo];
  const fonte: FonteBilancio | undefined = esercizio.fonti[voce.campo];
  // Spazio fisso per la sigla: le cifre restano allineate in colonna anche
  // dove la sigla manca.
  const marcatore = marcaFonte && (
    <span className="ml-1 inline-block w-2.5 text-left align-super text-caption font-semibold text-ink-3">
      {valore !== null && fonte && (
        <>
          <span aria-hidden>{FONTI_BILANCIO[fonte].sigla}</span>
          <span className="sr-only">, fonte: {FONTI_BILANCIO[fonte].etichetta}</span>
        </>
      )}
    </span>
  );

  if (valore === null) {
    return (
      <>
        <span className="text-ink-off" aria-hidden>
          —
        </span>
        <span className="sr-only">non disponibile</span>
        {marcatore}
      </>
    );
  }
  return (
    <>
      <span className="text-ink tabular-nums">
        {formatValoreBilancio(valore, voce.unita)}
      </span>
      {marcatore}
    </>
  );
}

/** Tabella pluriennale: una colonna per esercizio (dal più vecchio al più
 *  recente), una riga per voce. Le righe senza alcun valore non compaiono. */
export function BilanciTabella({ esercizi }: { esercizi: EsercizioBilancio[] }) {
  const captionId = useId();
  const scrollRef = useRef<HTMLDivElement>(null);

  const ordinati = [...esercizi].sort((a, b) => a.anno - b.anno);
  const anni = ordinati.map((e) => e.anno);
  const gruppi = GRUPPI_VOCI_BILANCIO.map((gruppo) => ({
    ...gruppo,
    voci: gruppo.voci.filter((voce) => ordinati.some((e) => e[voce.campo] !== null)),
  })).filter((gruppo) => gruppo.voci.length > 0);

  const fontiUsate = ORDINE_FONTI.filter((fonte) =>
    ordinati.some((e) =>
      gruppi.some((g) => g.voci.some((v) => e[v.campo] !== null && e.fonti[v.campo] === fonte)),
    ),
  );
  // Con una sola fonte la sigla su ogni cella sarebbe solo rumore (anche per
  // chi usa uno screen reader): basta dirlo una volta nella legenda.
  const marcaFonte = fontiUsate.length > 1;

  // Su schermi stretti si parte dagli anni più recenti, i più utili: la
  // prima colonna resta comunque visibile.
  const chiaveAnni = anni.join(",");
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [chiaveAnni]);

  if (gruppi.length === 0) return null;

  return (
    <div>
      <div
        ref={scrollRef}
        role="region"
        aria-labelledby={captionId}
        tabIndex={0}
        className="overflow-x-auto rounded-panel border border-line bg-sheet shadow-card focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <table className="w-full border-separate border-spacing-0 text-body">
          <caption id={captionId} className="sr-only">
            Bilanci per esercizio, {intervalloAnni(anni)}. Valori in euro, tranne il numero di
            dipendenti.
          </caption>
          <thead>
            <tr>
              <th
                scope="col"
                className={cn(
                  PRIMA_COLONNA,
                  "border-b border-line-control bg-desk py-3 text-small font-medium text-ink-3",
                )}
              >
                Voce
              </th>
              {ordinati.map((e) => {
                const tipo = TIPO_BILANCIO_LABELS[e.tipo_bilancio];
                return (
                  <th
                    key={e.anno}
                    scope="col"
                    className="min-w-30 whitespace-nowrap border-b border-line-control bg-desk px-4 py-3 text-right align-bottom font-normal"
                  >
                    <span className="block font-semibold text-ink tabular-nums">
                      {e.anno}
                    </span>
                    {chiusuraNonSolare(e.data_chiusura) && (
                      <span className="block text-small text-ink-3 tabular-nums">
                        chiuso il {formatDateNumeric(e.data_chiusura)}
                      </span>
                    )}
                    {tipo && <span className="block text-small text-ink-3">{tipo}</span>}
                  </th>
                );
              })}
            </tr>
          </thead>
          {gruppi.map((gruppo) => (
            <tbody key={gruppo.titolo}>
              <tr>
                <th
                  scope="rowgroup"
                  className={cn(
                    PRIMA_COLONNA,
                    "border-b border-line bg-desk pb-1.5 pt-4 text-small font-semibold text-ink",
                  )}
                >
                  {gruppo.titolo}
                </th>
                <td colSpan={anni.length} className="border-b border-line bg-desk" />
              </tr>
              {gruppo.voci.map((voce) => (
                <tr key={voce.campo}>
                  <th
                    scope="row"
                    className={cn(
                      PRIMA_COLONNA,
                      "border-b border-line bg-sheet py-2.5 font-normal text-ink-2",
                    )}
                  >
                    {voce.etichetta}
                  </th>
                  {ordinati.map((e) => (
                    <td
                      key={e.anno}
                      className="whitespace-nowrap border-b border-line px-4 py-2.5 text-right"
                    >
                      <Cella esercizio={e} voce={voce} marcaFonte={marcaFonte} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          ))}
        </table>
      </div>

      <div className="mt-2 flex flex-col gap-1 text-small text-ink-3">
        {marcaFonte ? (
          <>
            <p className="flex flex-wrap gap-x-4 gap-y-1">
              <span className="font-medium text-ink-2">Fonti:</span>
              {fontiUsate.map((fonte) => (
                <span key={fonte} className="whitespace-nowrap">
                  <span className="font-semibold text-ink-2">
                    {FONTI_BILANCIO[fonte].sigla}
                  </span>{" "}
                  = {FONTI_BILANCIO[fonte].etichetta}
                </span>
              ))}
            </p>
            <p>
              Se più fonti riportano la stessa voce vale la più affidabile: prima il bilancio
              ufficiale, poi la visura, poi lo storico del Registro Imprese.
            </p>
          </>
        ) : (
          fontiUsate.length === 1 && (
            <p>Fonte di tutti i valori: {FONTI_BILANCIO[fontiUsate[0]].etichetta}.</p>
          )
        )}
        <p>Valori in euro, tranne il numero di dipendenti. «—» indica un dato non disponibile.</p>
      </div>
    </div>
  );
}
