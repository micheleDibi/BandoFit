import { ArrowRight, CalendarClock, Handshake, Plus } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { CallStatoBadge } from "../components/partenariati/CallStatoBadge";
import { linkCall, passoDa } from "../components/partenariati/callDati";
import { AvvisoLimiteCall, RiepilogoLimiteCall, statoLimite, useLimiteCall } from "../components/partenariati/LimitiCall";
import { Schede } from "../components/partenariati/Schede";
import { LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { useMieCall } from "../hooks/useCallPartenariato";
import { useCompany } from "../hooks/useCompany";
import { apiErrorMessage } from "../lib/api";
import { CALL_COPY } from "../lib/copy";
import { formatDate } from "../lib/format";
import type { CallCard } from "../types";

type Vista = "mie";
/** Per ora solo «Le mie call»: le altre viste (per te, tutte, salvate,
 *  candidature…) arrivano con i WP successivi. */
const VISTE: Vista[] = ["mie"];

function RigaCall({ call }: { call: CallCard }) {
  const bozza = call.stato === "bozza";
  const passo = passoDa(null, call.wizard_passo ?? 1);
  return (
    <li>
      <Card className="p-4 transition-colors hover:border-brand-300">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <CallStatoBadge stato={call.stato} />
              {call.scadenza_call && call.stato === "pubblicata" && (
                <span className="inline-flex items-center gap-1 text-xs text-slate-500">
                  <CalendarClock className="size-3.5" aria-hidden />
                  Candidature fino al {formatDate(call.scadenza_call)}
                </span>
              )}
            </div>
            <h3 className="mt-1.5 font-display text-base font-semibold text-slate-900">
              <Link
                to={linkCall(call)}
                className="rounded hover:text-brand-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
              >
                {call.titolo || "Call senza titolo"}
              </Link>
            </h3>
            <p className="mt-0.5 text-sm text-slate-600">Bando: {call.bando.titolo}</p>
            <p className="mt-1 text-xs text-slate-500">
              {bozza
                ? `Bozza ferma al passo ${passo} di 7: ${CALL_COPY.passi[passo - 1]}`
                : `${call.posizioni_n} ${call.posizioni_n === 1 ? "posizione" : "posizioni"} · ${call.requisiti_cercati_n} ${call.requisiti_cercati_n === 1 ? "requisito cercato" : "requisiti cercati"}`}
              {call.updated_at ? ` · aggiornata il ${formatDate(call.updated_at)}` : ""}
            </p>
          </div>
          <LinkButton to={linkCall(call)} variant="secondary" size="sm" aria-label={`${bozza ? "Riprendi" : "Apri"}: ${call.titolo || call.bando.titolo}`}>
            {bozza ? "Riprendi" : "Apri"}
            <ArrowRight className="size-4" aria-hidden />
          </LinkButton>
        </div>
      </Card>
    </li>
  );
}

function LeMieCall() {
  const { data, isPending, isError, error, refetch } = useMieCall();
  if (isPending) {
    return (
      <div className="space-y-3" aria-hidden>
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }
  if (isError) {
    return (
      <ErrorState
        message={apiErrorMessage(error, "Impossibile caricare le tue call.")}
        onRetry={() => void refetch()}
      />
    );
  }
  if (data.items.length === 0) {
    return (
      <EmptyState
        title="Non hai ancora creato call"
        description="Una call di partenariato ti aiuta a trovare le aziende con cui partecipare a un bando. Parti dal bando che ti interessa."
        action={
          <LinkButton to="/app/bandi?partenariato=ammesso" variant="secondary">
            Cerca un bando
          </LinkButton>
        }
      />
    );
  }
  return (
    <div className="space-y-3">
      <ul className="space-y-3">
        {data.items.map((c) => (
          <RigaCall key={c.id} call={c} />
        ))}
      </ul>
      {data.total > data.items.length && (
        <p className="text-xs text-slate-500">
          Mostriamo le {data.items.length} call più recenti su {data.total}.
        </p>
      )}
    </div>
  );
}

/** Pagina del modulo partenariati (`?vista=mie`). */
export default function Partenariati() {
  const [params, setParams] = useSearchParams();
  const { data: azienda } = useCompany();
  const limite = useLimiteCall();
  const mie = useMieCall();
  const vista: Vista = VISTE.includes(params.get("vista") as Vista) ? (params.get("vista") as Vista) : "mie";
  const editable = azienda?.editable ?? false;
  const puoCreare = editable && statoLimite(limite) !== "non_incluso";

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="inline-flex items-center gap-2 font-display text-2xl font-bold tracking-tight text-slate-900">
            <Handshake className="size-6 text-brand-500" aria-hidden />
            Partenariati
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            Cerca le aziende con cui partecipare a un bando: pubblica una call, in forma anonima, e
            scegli tu con chi parlare.{" "}
            <Link to="/app/azienda#partner" className="font-medium text-brand-600 hover:text-brand-700">
              Il tuo profilo partner
            </Link>
          </p>
        </div>
        {puoCreare && (
          <LinkButton to="/app/partenariati/call/nuova">
            <Plus className="size-4" aria-hidden />
            Crea una call
          </LinkButton>
        )}
      </div>

      <div className="space-y-2">
        <RiepilogoLimiteCall limite={limite} />
        <AvvisoLimiteCall limite={limite} editable={editable} />
      </div>

      <Schede
        etichetta="Viste dei partenariati"
        schede={[{ id: "mie", etichetta: "Le mie call", conteggio: mie.data?.total }]}
        attiva={vista}
        onCambia={(v) =>
          setParams(
            (p) => {
              const nuovi = new URLSearchParams(p);
              nuovi.set("vista", v);
              return nuovi;
            },
            { replace: true },
          )
        }
      >
        {vista === "mie" && <LeMieCall />}
      </Schede>
    </div>
  );
}
