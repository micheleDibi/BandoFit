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
import { ErrorState, Skeleton } from "../../ui/states";
import { importoCents, numero, thClass } from "./comuni";

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
        <p id={idErrore} className="w-full text-sm text-red-600" role="alert">
          {errore}
        </p>
      )}
    </form>
  );
}

function Tessera({ titolo, valore, nota }: { titolo: string; valore: ReactNode; nota?: ReactNode }) {
  return (
    <Card className="p-4">
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{titolo}</dt>
      <dd className="mt-1">
        <span className="font-display text-2xl font-bold tabular text-slate-900">{valore}</span>
        {nota && <span className="mt-1 block text-xs text-slate-500">{nota}</span>}
      </dd>
    </Card>
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
    <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
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
    return <p className="text-sm text-slate-500">Nessun costo del modulo nel periodo.</p>;
  }
  return (
    <div className="space-y-4">
      {valute.map((valuta) => {
        const voci = c.voci.filter((v) => v.valuta === valuta);
        const totale = c.totali.find((t) => t.valuta === valuta);
        return (
          <Card key={valuta} className="overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-sm">
                <caption className="px-4 pt-4 text-left font-display text-base font-semibold text-slate-900">
                  {VALUTE[valuta] ?? valuta}
                </caption>
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50/70 text-xs uppercase tracking-wide text-slate-500">
                    <th scope="col" className={thClass}>Fornitore</th>
                    <th scope="col" className={thClass}>Servizio</th>
                    <th scope="col" className={thClass}>Esito</th>
                    <th scope="col" className={cn(thClass, "text-right")}>Chiamate</th>
                    <th scope="col" className={cn(thClass, "text-right")}>Costo</th>
                  </tr>
                </thead>
                <tbody>
                  {voci.map((v) => (
                    <tr key={`${v.provider}|${v.service}|${v.outcome}`} className="border-b border-slate-100">
                      <td className="px-4 py-2.5 text-slate-700">
                        {ADMIN_PARTENARIATI_COPY.providers[v.provider] ?? v.provider}
                      </td>
                      <th scope="row" className="px-4 py-2.5 text-left font-normal text-slate-800">
                        {ADMIN_PARTENARIATI_COPY.servizi[v.service] ?? v.service}
                      </th>
                      <td className="px-4 py-2.5 text-slate-700">
                        {ADMIN_PARTENARIATI_COPY.esiti[v.outcome] ?? v.outcome}
                      </td>
                      <td className="px-4 py-2.5 text-right tabular text-slate-700">{numero(v.eventi)}</td>
                      <td className="px-4 py-2.5 text-right tabular font-medium text-slate-900">
                        {importoCents(v.cost_cents, valuta)}
                      </td>
                    </tr>
                  ))}
                </tbody>
                {totale && (
                  <tfoot>
                    <tr className="bg-slate-50/70">
                      <th scope="row" colSpan={3} className="px-4 py-2.5 text-left font-semibold text-slate-900">
                        Totale in {valuta === "EUR" ? "euro" : valuta === "USD" ? "dollari" : valuta}
                      </th>
                      <td className="px-4 py-2.5 text-right tabular font-semibold text-slate-900">
                        {numero(totale.eventi)}
                      </td>
                      <td className="px-4 py-2.5 text-right tabular font-semibold text-slate-900">
                        {importoCents(totale.cost_cents, valuta)}
                      </td>
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          </Card>
        );
      })}
    </div>
  );
}

function Intestazione({ periodo }: { periodo: Periodo }) {
  return (
    <p className="text-sm text-slate-500">
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
    <div className="space-y-4">
      <SceltaPeriodo periodo={periodo} onApplica={setPeriodo} />
      <Intestazione periodo={periodo} />
      <p className="text-xs text-slate-500">{ADMIN_PARTENARIATI_COPY.notaMetriche}</p>
      {q.isPending ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-hidden>
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
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
    <div className="space-y-4">
      <SceltaPeriodo periodo={periodo} onApplica={setPeriodo} />
      <Intestazione periodo={periodo} />
      <p className="text-xs text-slate-500">{ADMIN_PARTENARIATI_COPY.notaValute}</p>
      {q.isPending ? (
        <Skeleton className="h-48 w-full" />
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
