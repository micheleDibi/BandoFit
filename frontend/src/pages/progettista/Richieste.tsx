import { CalendarClock } from "lucide-react";
import { Link } from "react-router-dom";
import { ConsulenzaStatoBadge } from "../Consulenze";
import { AiEsitoBadge } from "../../components/bandi/badges";
import { Badge } from "../../components/ui/Badge";
import { Card } from "../../components/ui/Card";
import { EmptyState, ErrorState, Skeleton } from "../../components/ui/states";
import { useRichiestePool } from "../../hooks/useProgettistaRichieste";
import { apiErrorMessage } from "../../lib/api";
import { PROPOSTA_STATO_LABELS } from "../../lib/copy";
import { formatDate, formatSlotGiorno, formatSlotOra } from "../../lib/format";
import { scoreColorClasses } from "../../lib/scoreColor";
import type { RichiestaPool } from "../../types";

function RichiestaCard({ richiesta }: { richiesta: RichiestaPool }) {
  return (
    <Link
      to={`/app/progettista/richieste/${richiesta.id}`}
      className="block rounded-2xl focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
    >
      <Card className="p-5 transition-shadow hover:shadow-md">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-medium text-slate-900">
              {richiesta.ragione_sociale ?? richiesta.denominazione_utente}
            </p>
            <p className="mt-0.5 text-xs text-slate-500">
              {richiesta.partita_iva && (
                <span className="tabular">P.IVA {richiesta.partita_iva} · </span>
              )}
              richiesta del {formatDate(richiesta.created_at)}
            </p>
          </div>
          {richiesta.assegnata_a_me ? (
            <ConsulenzaStatoBadge stato={richiesta.stato} />
          ) : richiesta.mia_proposta_stato ? (
            <Badge tone={richiesta.mia_proposta_stato === "inviata" ? "brand" : "slate"}>
              Proposta: {PROPOSTA_STATO_LABELS[richiesta.mia_proposta_stato].toLowerCase()}
            </Badge>
          ) : null}
        </div>
        <p className="mt-2 text-sm text-slate-700">{richiesta.bando_titolo}</p>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm">
          {richiesta.esito && <AiEsitoBadge esito={richiesta.esito} />}
          {richiesta.punteggio !== null && (
            <span
              className={`tabular font-display font-bold ${scoreColorClasses(richiesta.punteggio).text}`}
            >
              {richiesta.punteggio}
              <span className="text-xs font-medium text-slate-400">/100</span>
            </span>
          )}
          {richiesta.appuntamento && (
            <span className="inline-flex items-center gap-1.5 text-slate-600">
              <CalendarClock className="size-4 text-slate-400" aria-hidden />
              {formatSlotGiorno(richiesta.appuntamento.inizio)},{" "}
              {formatSlotOra(richiesta.appuntamento.inizio)}
            </span>
          )}
        </div>
      </Card>
    </Link>
  );
}

/** Pool delle richieste di consulto: quelle aperte a tutti i progettisti e
 *  quelle assegnate a chi guarda. */
export default function Richieste() {
  const { data, isPending, isError, error, refetch } = useRichiestePool();

  return (
    <div className="mx-auto max-w-4xl">
      <h1 className="font-display text-2xl font-bold tracking-tight text-slate-900">
        Richieste di consulto
      </h1>
      <p className="mt-1 text-sm text-slate-500">
        Le aziende che hanno chiesto un consulto dopo un AI-check. Invia una proposta:
        se il titolare la accetta, la consulenza è assegnata a te.
      </p>

      {isPending ? (
        <div className="mt-6 space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      ) : isError ? (
        <div className="mt-6">
          <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
        </div>
      ) : (
        <>
          {data && data.assegnate.length > 0 && (
            <section className="mt-6" aria-label="Consulenze assegnate a te">
              <h2 className="font-display text-lg font-bold tracking-tight text-slate-900">
                Assegnate a te
              </h2>
              <div className="mt-3 space-y-3">
                {data.assegnate.map((r) => (
                  <RichiestaCard key={r.id} richiesta={r} />
                ))}
              </div>
            </section>
          )}

          <section className="mt-6" aria-label="Richieste aperte">
            <h2 className="font-display text-lg font-bold tracking-tight text-slate-900">
              Richieste aperte
            </h2>
            {!data || data.aperte.length === 0 ? (
              <div className="mt-3">
                <EmptyState
                  title="Nessuna richiesta aperta"
                  description="Quando un'azienda attiverà il consulto esperto la troverai qui (e riceverai una notifica)."
                />
              </div>
            ) : (
              <div className="mt-3 space-y-3">
                {data.aperte.map((r) => (
                  <RichiestaCard key={r.id} richiesta={r} />
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
