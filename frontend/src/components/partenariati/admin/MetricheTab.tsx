import { useId, useState, type ReactNode } from "react";
import {
  type Periodo,
  useCostiPartenariati,
  useMetrichePartenariati,
} from "../../../hooks/useAdminPartenariati";
import { apiErrorMessage } from "../../../lib/api";
import { cn } from "../../../lib/cn";
import { ADMIN_PARTENARIATI_COPY } from "../../../lib/copy";
import { formatDate, todayItalyIso } from "../../../lib/format";
import type { AccettazionePartenariati, CostiPartenariati, MetrichePartenariati } from "../../../types";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { TextField } from "../../ui/Field";
import { InlineError } from "../../ui/InlineError";
import { ErrorState, Skeleton } from "../../ui/states";
import { Table, Td, Th } from "../../ui/Table";
import { importoCents, numero, thRigaClass } from "./comuni";

/** Periodo predefinito: gli ultimi 90 giorni (giorni italiani). */
const GIORNI_DEFAULT = 90;

function giorniPrima(iso: string, giorni: number): string {
  const [a, m, g] = iso.split("-").map(Number);
  return new Date(Date.UTC(a, m - 1, g - giorni)).toISOString().slice(0, 10);
}

export function periodoPredefinito(): Periodo {
  const oggi = todayItalyIso();
  return { da: giorniPrima(oggi, GIORNI_DEFAULT - 1), a: oggi };
}

/** Scelta del periodo (estremi compresi): si applica con «Aggiorna», così
 *  non parte una richiesta a ogni cifra digitata. */
function SceltaPeriodo({ periodo, onApplica }: { periodo: Periodo; onApplica: (p: Periodo) => void }) {
  const idErrore = useId();
  const [da, setDa] = useState(periodo.da);
  const [a, setA] = useState(periodo.a);
  const [errore, setErrore] = useState<string | null>(null);
  const applica = () => {
    if (!da || !a) {
      setErrore("Indica le due date.");
      return;
    }
    if (da > a) {
      setErrore("La data di inizio deve venire prima di quella di fine.");
      return;
    }
    setErrore(null);
    onApplica({ da, a });
  };
  return (
    <form
      noValidate
      className="flex flex-wrap items-end gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        applica();
      }}
    >
      <div className="w-44">
        <TextField label="Dal" type="date" value={da} max={a || undefined} onChange={(e) => setDa(e.target.value)} />
      </div>
      <div className="w-44">
        <TextField label="Al" type="date" value={a} min={da || undefined} onChange={(e) => setA(e.target.value)} />
      </div>
      <Button type="submit" variant="secondary" aria-describedby={errore ? idErrore : undefined}>
        Aggiorna
      </Button>
      {errore && (
        <InlineError id={idErrore} className="w-full">
          {errore}
        </InlineError>
      )}
    </form>
  );
}

/** Una cifra della griglia: etichetta sopra, cifra in evidenza, nota sotto
 *  (le note sono frasi intere: in `Facts`, accanto al valore, non ci stanno). */
function Tessera({ titolo, valore, nota }: { titolo: string; valore: ReactNode; nota?: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-small text-ink-3">{titolo}</dt>
      <dd className="flex flex-col gap-1">
        <span className="text-figure text-ink tabular-nums">{valore}</span>
        {nota && <span className="text-small text-ink-3">{nota}</span>}
      </dd>
    </div>
  );
}

const percentuale = (valore: number | null | undefined, giaPercentuale = false) =>
  valore === null || valore === undefined ? "—" : `${numero(giaPercentuale ? valore : valore * 100, 1)}%`;

function tasso(a: AccettazionePartenariati | undefined): { valore: string; nota: string } {
  if (!a) return { valore: "—", nota: "Nessuna decisione" };
  const decise = a.accettate + a.rifiutate;
  return {
    valore: percentuale(a.tasso),
    nota:
      decise === 0
        ? "Nessuna decisione"
        : `${numero(a.accettate)} accettate su ${numero(decise)} decise`,
  };
}

function Metriche({ m }: { m: MetrichePartenariati }) {
  const cand = tasso(m.accettazione?.candidatura);
  const inv = tasso(m.accettazione?.invito);
  const ore = m.ore_mediane_prima_candidatura;
  return (
    <Card className="sm:p-6">
      <dl className="grid gap-x-10 gap-y-6 sm:grid-cols-2 lg:grid-cols-3">
        <Tessera titolo="Call pubblicate" valore={numero(m.call_pubblicate)} />
        <Tessera
          titolo="Candidature per call"
          valore={numero(m.candidature_per_call, 2)}
          nota={`${numero(m.candidature)} candidature spontanee e ${numero(m.inviti)} inviti in tutto`}
        />
        <Tessera
          titolo="Call con una candidatura entro 30 giorni"
          valore={percentuale(m.percentuale_call_con_candidatura_30_giorni, true)}
          nota={`${numero(m.call_con_candidatura_30_giorni)} su ${numero(m.call_osservabili_30_giorni)} call pubblicate da almeno 30 giorni`}
        />
        <Tessera titolo="Candidature accettate" valore={cand.valore} nota={cand.nota} />
        <Tessera titolo="Inviti accettati" valore={inv.valore} nota={inv.nota} />
        <Tessera
          titolo="Tempo alla prima candidatura"
          valore={ore === null ? "—" : `${numero(ore, 1)} ore`}
          nota={
            ore === null
              ? "Nessuna candidatura spontanea"
              : `Mediana, circa ${numero(ore / 24, 1)} giorni; solo candidature spontanee`
          }
        />
        <Tessera
          titolo="Copertura media dei requisiti cercati"
          valore={percentuale(m.copertura_media_gap)}
          nota="Quota dei requisiti cercati coperti dal consorzio"
        />
        <Tessera
          titolo="Consorzi in regola"
          valore={numero(m.consorzi_validati_verde)}
          nota={`su ${numero(m.consorzi_validati)} consorzi verificati (ultima verifica salvata)`}
        />
      </dl>
    </Card>
  );
}

const VALUTE: Record<string, string> = {
  EUR: "Euro (openapi.it)",
  USD: "Dollari (Anthropic)",
};

/** Costi per valuta: una tabella per valuta, con il totale della SOLA valuta
 *  (mai sommati tra euro e dollari). */
function Costi({ c }: { c: CostiPartenariati }) {
  const valute = [...new Set([...c.totali.map((t) => t.valuta), ...c.voci.map((v) => v.valuta)])];
  if (valute.length === 0) {
    return (
      <Card>
        <p className="text-body text-ink-2">Nessun costo del modulo nel periodo.</p>
      </Card>
    );
  }
  return (
    <div className="flex flex-col gap-6">
      {valute.map((valuta) => {
        const voci = c.voci.filter((v) => v.valuta === valuta);
        const totale = c.totali.find((t) => t.valuta === valuta);
        return (
          // Una Card con ombra per valuta, come le tabelle delle altre pagine Admin:
          // la didascalia in testa, nessun filetto sotto l'ultima riga.
          <Card key={valuta} className="overflow-hidden p-0">
            <Table
              className="min-w-[640px] [&>:last-child>tr:last-child>*]:border-b-0"
              classNameContenitore="px-2"
            >
              <caption className="px-3 pt-4 pb-3 text-left text-title-group text-ink">
                {VALUTE[valuta] ?? valuta}
              </caption>
              <thead>
                <tr>
                  <Th>Fornitore</Th>
                  <Th>Servizio</Th>
                  <Th>Esito</Th>
                  <Th numerica>Chiamate</Th>
                  <Th numerica>Costo</Th>
                </tr>
              </thead>
              <tbody>
                {voci.map((v) => (
                  <tr key={`${v.provider}|${v.service}|${v.outcome}`}>
                    <Td className="text-ink-2">
                      {ADMIN_PARTENARIATI_COPY.providers[v.provider] ?? v.provider}
                    </Td>
                    <th scope="row" className={thRigaClass}>
                      {ADMIN_PARTENARIATI_COPY.servizi[v.service] ?? v.service}
                    </th>
                    <Td className="text-ink-2">
                      {ADMIN_PARTENARIATI_COPY.esiti[v.outcome] ?? v.outcome}
                    </Td>
                    <Td numerica className="text-ink-2">
                      {numero(v.eventi)}
                    </Td>
                    <Td numerica className="font-medium">
                      {importoCents(v.cost_cents, valuta)}
                    </Td>
                  </tr>
                ))}
              </tbody>
              {totale && (
                <tfoot>
                  <tr className="bg-desk">
                    <th scope="row" colSpan={3} className={cn(thRigaClass, "font-semibold")}>
                      Totale in {valuta === "EUR" ? "euro" : valuta === "USD" ? "dollari" : valuta}
                    </th>
                    <Td numerica className="font-semibold">
                      {numero(totale.eventi)}
                    </Td>
                    <Td numerica className="font-semibold">
                      {importoCents(totale.cost_cents, valuta)}
                    </Td>
                  </tr>
                </tfoot>
              )}
            </Table>
          </Card>
        );
      })}
    </div>
  );
}

function Intestazione({ periodo }: { periodo: Periodo }) {
  return (
    <p className="text-body text-ink-2">
      Dal {formatDate(periodo.da)} al {formatDate(periodo.a)}, estremi compresi.
    </p>
  );
}

/** Metriche del modulo sulle call pubblicate nel periodo (calcolate dal
 *  database: nessun limite di righe). */
export function MetricheTab() {
  const [periodo, setPeriodo] = useState<Periodo>(periodoPredefinito);
  const q = useMetrichePartenariati(periodo, true);
  return (
    <div className="flex flex-col gap-4">
      <SceltaPeriodo periodo={periodo} onApplica={setPeriodo} />
      <Intestazione periodo={periodo} />
      <p className="text-small text-ink-3">{ADMIN_PARTENARIATI_COPY.notaMetriche}</p>
      {q.isPending ? (
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3" aria-hidden>
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-control" />
          ))}
        </div>
      ) : q.isError ? (
        <ErrorState message={apiErrorMessage(q.error)} onRetry={() => void q.refetch()} />
      ) : (
        <div className={cn(q.isPlaceholderData && "opacity-60 transition-opacity")} aria-busy={q.isPlaceholderData}>
          <Metriche m={q.data} />
        </div>
      )}
    </div>
  );
}

/** Costi del modulo da `api_usage_events`, per fornitore, servizio, esito e
 *  valuta. */
export function CostiTab() {
  const [periodo, setPeriodo] = useState<Periodo>(periodoPredefinito);
  const q = useCostiPartenariati(periodo, true);
  return (
    <div className="flex flex-col gap-4">
      <SceltaPeriodo periodo={periodo} onApplica={setPeriodo} />
      <Intestazione periodo={periodo} />
      <p className="text-small text-ink-3">{ADMIN_PARTENARIATI_COPY.notaValute}</p>
      {q.isPending ? (
        <Skeleton className="h-48 w-full rounded-panel" />
      ) : q.isError ? (
        <ErrorState message={apiErrorMessage(q.error)} onRetry={() => void q.refetch()} />
      ) : (
        <div className={cn(q.isPlaceholderData && "opacity-60 transition-opacity")} aria-busy={q.isPlaceholderData}>
          <Costi c={q.data} />
        </div>
      )}
    </div>
  );
}
