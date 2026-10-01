import { useState, type ReactNode } from "react";
import { useBilanci, useRecuperaBilanci } from "../../../hooks/useBilanci";
import { useFunzioni } from "../../../hooks/useFunzioni";
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
import { Alert } from "../../ui/Alert";
import { Badge } from "../../ui/Badge";
import { InlineError } from "../../ui/InlineError";
import { Section, SectionHeader } from "../../ui/SectionHeader";
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
    <Section>
      <SectionHeader livello={3} titolo="Sintesi per fasce" />
      {/* Ogni fascia viene dall'ultimo esercizio che ha quel dato (il
          patrimonio netto, per esempio, spesso solo dalla visura): l'anno
          di riferimento è quello del fatturato e dell'andamento. */}
      <p className="text-small text-ink-3">
        {fasce.fatturato && fasce.anno_riferimento
          ? `Fatturato e andamento riferiti all'esercizio ${fasce.anno_riferimento}; le altre voci all'ultimo esercizio che le riporta.`
          : "Ogni voce è riferita all'ultimo esercizio che la riporta."}
      </p>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
        {voci.map((v) => (
          <div key={v.tipo} className="flex flex-col gap-0.5">
            <dt className="text-small text-ink-3">{FASCE_TITOLI[v.tipo]}</dt>
            <dd className="text-body font-medium text-ink">{v.valore}</dd>
          </div>
        ))}
      </dl>
    </Section>
  );
}

/** «Bilanci» della pagina Azienda (ancora `#bilanci`): tabella per esercizio,
 *  andamento del fatturato, fasce e indicatori, oppure il motivo per cui
 *  mancano e la CTA di recupero (solo titolare); in fondo la card del bilancio
 *  ufficiale. CTA e card solo a storico acceso. Logica di recupero invariata. */
export function BilanciSection() {
  const { data, isPending, isError, error, refetch } = useBilanci();
  const recupera = useRecuperaBilanci();
  const { bilanciStoricoAttivo } = useFunzioni();
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
      <div className="flex flex-col gap-3" aria-hidden>
        <Skeleton className="h-64 w-full" />
        <div className="grid gap-3 sm:grid-cols-3">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      </div>
    );
  } else if (isError || !data) {
    corpo = (
      <ErrorState
        title="Non siamo riusciti a caricare i bilanci."
        message={apiErrorMessage(error)}
        onRetry={() => refetch()}
      />
    );
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
      <div className="flex flex-col gap-6">
        <BilanciNonDisponibili
          data={data}
          onRecupera={handleRecupera}
          recuperoInCorso={recupera.isPending}
        />
        <BilanciTabella esercizi={data.esercizi} />
        <div className={cn("grid gap-6", conTrend && data.fasce && "lg:grid-cols-[3fr_2fr]")}>
          {conTrend && (
            <Section>
              <SectionHeader livello={3} titolo="Fatturato negli anni" />
              <TrendFatturato esercizi={data.esercizi} />
            </Section>
          )}
          {data.fasce && <SintesiFasce fasce={data.fasce} />}
        </div>
        {data.indicatori.length > 0 && (
          <Section>
            <SectionHeader livello={3} titolo="Indicatori" />
            <p className="text-small text-ink-3">
              Calcolati sugli esercizi disponibili, con numeratore e denominatore dello stesso
              anno.
            </p>
            <IndicatoriBilancio indicatori={data.indicatori} />
          </Section>
        )}
      </div>
    );
  }

  return (
    <Section id="bilanci" aria-labelledby="bilanci-titolo" className="scroll-mt-16">
      <SectionHeader id="bilanci-titolo" titolo="Bilanci" />
      {disponibili ? (
        <div className="flex flex-wrap items-center gap-2">
          <Badge>{anni.length === 1 ? "1 esercizio" : `${anni.length} esercizi`}</Badge>
          {data.sandbox && <Badge>Dati di test</Badge>}
          <span className="text-small text-ink-3">Registro Imprese, {intervalloAnni(anni)}</span>
        </div>
      ) : (
        <p className="text-body text-ink-2">
          {bilanciStoricoAttivo
            ? "I bilanci depositati negli ultimi anni: fatturato, utile, patrimonio e dipendenti, esercizio per esercizio."
            : "I bilanci depositati: fatturato, utile, patrimonio e dipendenti, esercizio per esercizio."}
        </p>
      )}

      {/* Sempre montata: il componente con il bottone può sparire quando i
          bilanci arrivano, e l'esito deve essere annunciato comunque. */}
      <div role="status" aria-live="polite">
        {recupera.isPending ? (
          <p className="rounded-control bg-sunken px-3 py-2 text-small text-ink-2">
            Stiamo recuperando i bilanci dal Registro Imprese: può richiedere fino a un minuto.
          </p>
        ) : (
          esito && (
            <Alert tono={esito.riuscito ? "ok" : "attenzione"} ruolo="none">
              {esito.testo}
            </Alert>
          )
        )}
      </div>
      {errore && <InlineError>{errore}</InlineError>}

      {corpo}

      {/* Bilancio ufficiale on-demand: dati propri, indipendenti dallo stato
          dei bilanci qui sopra (serve proprio quando mancano). La card si
          nasconde da sola se l'addon non è a catalogo; a storico spento non
          si monta (le sue rotte rispondono 404). */}
      {bilanciStoricoAttivo && <BilancioUfficialeCard className="mt-2" />}
    </Section>
  );
}
