import { useState, type ReactNode } from "react";
import { useBilanci, useRecuperaBilanci } from "../../../hooks/useBilanci";
import { apiErrorMessage } from "../../../lib/api";
import {
  FASCE_TITOLI,
  etichettaFascia,
  intervalloAnni,
  type TipoFascia,
} from "../../../lib/bilanci";
import { cn } from "../../../lib/cn";
import { BILANCI_COPY } from "../../../lib/copy";
import type { BilanciOut, FasceBilancio } from "../../../types";
import { Badge } from "../../ui/Badge";
import { Card } from "../../ui/Card";
import { ErrorState, Skeleton } from "../../ui/states";
import { BilanciNonDisponibili } from "./BilanciNonDisponibili";
import { BilanciTabella } from "./BilanciTabella";
import { BilancioUfficialeCard } from "./BilancioUfficialeCard";
import { IndicatoriBilancio } from "./IndicatoriBilancio";
import { TrendFatturato } from "./TrendFatturato";

const ORDINE_FASCE: TipoFascia[] = [
  "fatturato",
  "trend_fatturato",
  "patrimonio_netto",
  "dipendenti",
];

interface Esito {
  testo: string;
  riuscito: boolean;
}

/** Messaggio da annunciare a recupero concluso, letto dalla risposta. Deve
 *  dire la stessa cosa della tabella sotto: gli anni recuperati sono quelli
 *  dello storico (fonte it_advanced), non l'esercizio che c'era già. */
function esitoRecupero(data: BilanciOut): Esito {
  const conEsercizi = data.stato === "disponibili" && data.esercizi.length > 0;
  if (conEsercizi && data.storico_esito === "ok") {
    const dalloStorico = data.esercizi
      .filter((e) => Object.values(e.fonti).includes("it_advanced"))
      .map((e) => e.anno);
    if (dalloStorico.length === 0) {
      return { testo: BILANCI_COPY.nessunAltroBilancio, riuscito: false };
    }
    const n = dalloStorico.length;
    const anni = intervalloAnni(dalloStorico);
    return {
      testo:
        n === 1
          ? `Bilanci recuperati: 1 esercizio (${anni}).`
          : `Bilanci recuperati: ${n} esercizi (${anni}).`,
      riuscito: true,
    };
  }
  if (conEsercizi) {
    const motivo =
      data.motivo === "nessun_bilancio"
        ? ` ${BILANCI_COPY.nessunAltroBilancio}`
        : data.motivo
          ? ` ${BILANCI_COPY.motivi[data.motivo]}`
          : "";
    return { testo: `Non è stato possibile recuperare lo storico.${motivo}`, riuscito: false };
  }
  const motivo = data.motivo ? ` ${BILANCI_COPY.motivi[data.motivo]}` : "";
  return { testo: `Nessun bilancio recuperato.${motivo}`, riuscito: false };
}

function SintesiFasce({ fasce }: { fasce: FasceBilancio }) {
  const voci = ORDINE_FASCE.map((tipo) => ({
    tipo,
    valore: etichettaFascia(tipo, fasce[tipo]),
  })).filter((v) => v.valore !== null);
  if (voci.length === 0) return null;
  return (
    <Card className="p-5">
      <h3 className="font-display text-sm font-semibold text-slate-900">Sintesi per fasce</h3>
      {/* Ogni fascia viene dall'ultimo esercizio che ha quel dato (il
          patrimonio netto, per esempio, spesso solo dalla visura): l'anno
          di riferimento è quello del fatturato e dell'andamento. */}
      <p className="mt-0.5 text-xs text-slate-500">
        {fasce.fatturato && fasce.anno_riferimento
          ? `Fatturato e andamento riferiti all'esercizio ${fasce.anno_riferimento}; le altre voci all'ultimo esercizio che le riporta.`
          : "Ogni voce è riferita all'ultimo esercizio che la riporta."}
      </p>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3">
        {voci.map((v) => (
          <div key={v.tipo}>
            <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">
              {FASCE_TITOLI[v.tipo]}
            </dt>
            <dd className="mt-0.5 text-sm font-medium text-slate-800">{v.valore}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

/** Sezione «Bilanci» della pagina Azienda (ancora `#bilanci`): tabella per
 *  esercizio, andamento del fatturato, fasce e indicatori, oppure il motivo
 *  per cui mancano e la CTA di recupero (solo titolare); in fondo la card
 *  del bilancio ufficiale. */
export function BilanciSection() {
  const { data, isPending, isError, error, refetch } = useBilanci();
  const recupera = useRecuperaBilanci();
  const [esito, setEsito] = useState<Esito | null>(null);
  const [errore, setErrore] = useState<string | null>(null);

  const handleRecupera = async () => {
    if (recupera.isPending) return; // doppio click: la chiamata ha un costo
    setEsito(null);
    setErrore(null);
    try {
      setEsito(esitoRecupero(await recupera.mutateAsync()));
    } catch (err) {
      setErrore(apiErrorMessage(err));
    }
  };

  const disponibili = data?.stato === "disponibili" && data.esercizi.length > 0;
  const anni = disponibili ? data.esercizi.map((e) => e.anno) : [];
  const conTrend = disponibili && data.esercizi.filter((e) => e.fatturato !== null).length >= 2;

  let corpo: ReactNode;
  if (isPending) {
    corpo = (
      <div className="space-y-3" aria-hidden>
        <Skeleton className="h-64 w-full" />
        <div className="grid gap-3 sm:grid-cols-3">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      </div>
    );
  } else if (isError || !data) {
    corpo = <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />;
  } else if (!disponibili) {
    corpo = (
      <BilanciNonDisponibili
        data={data}
        onRecupera={handleRecupera}
        recuperoInCorso={recupera.isPending}
      />
    );
  } else {
    corpo = (
      <div className="space-y-4">
        <BilanciNonDisponibili
          data={data}
          onRecupera={handleRecupera}
          recuperoInCorso={recupera.isPending}
        />
        <BilanciTabella esercizi={data.esercizi} />
        <div className={cn("grid gap-4", conTrend && data.fasce && "lg:grid-cols-[3fr_2fr]")}>
          {conTrend && (
            <Card className="p-5">
              <h3 className="font-display text-sm font-semibold text-slate-900">
                Fatturato negli anni
              </h3>
              <div className="mt-4">
                <TrendFatturato esercizi={data.esercizi} />
              </div>
            </Card>
          )}
          {data.fasce && <SintesiFasce fasce={data.fasce} />}
        </div>
        {data.indicatori.length > 0 && (
          <div>
            <h3 className="font-display text-base font-semibold text-slate-900">Indicatori</h3>
            <p className="mt-0.5 text-sm text-slate-500">
              Calcolati sugli esercizi disponibili, con numeratore e denominatore dello stesso
              anno.
            </p>
            <div className="mt-3">
              <IndicatoriBilancio indicatori={data.indicatori} />
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <section id="bilanci" aria-labelledby="bilanci-titolo" className="mt-10 scroll-mt-24">
      <div>
        <h2
          id="bilanci-titolo"
          className="font-display text-xl font-bold tracking-tight text-slate-900"
        >
          Bilanci
        </h2>
        {disponibili ? (
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <Badge tone="brand">
              {anni.length === 1 ? "1 esercizio" : `${anni.length} esercizi`}
            </Badge>
            {data.sandbox && <Badge tone="amber">Dati di test</Badge>}
            <span className="text-xs text-slate-400">
              Registro Imprese · {intervalloAnni(anni)}
            </span>
          </div>
        ) : (
          <p className="mt-1 text-sm text-slate-500">
            I bilanci depositati negli ultimi anni: fatturato, utile, patrimonio e dipendenti,
            esercizio per esercizio.
          </p>
        )}
      </div>

      {/* Sempre montata: il componente con il bottone può sparire quando i
          bilanci arrivano, e l'esito deve essere annunciato comunque. */}
      <div role="status" aria-live="polite">
        {recupera.isPending ? (
          <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">
            Stiamo recuperando i bilanci dal Registro Imprese: può richiedere fino a un minuto.
          </p>
        ) : (
          esito && (
            <p
              className={cn(
                "mt-3 rounded-lg px-3 py-2 text-sm",
                esito.riuscito ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-800",
              )}
            >
              {esito.testo}
            </p>
          )
        )}
      </div>
      {errore && (
        <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {errore}
        </p>
      )}

      <div className="mt-4">{corpo}</div>

      {/* Bilancio ufficiale on-demand: dati propri, indipendenti dallo stato
          dei bilanci qui sopra (serve proprio quando mancano). La card si
          nasconde da sola se l'addon non è a catalogo. */}
      <BilancioUfficialeCard className="mt-6" />
    </section>
  );
}
