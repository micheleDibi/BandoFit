import { CalendarClock, MessagesSquare } from "lucide-react";
import { Link } from "react-router-dom";
import { Badge } from "../components/ui/Badge";
import { Card } from "../components/ui/Card";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { useConsulenze } from "../hooks/useConsulenze";
import { apiErrorMessage } from "../lib/api";
import { CONSULENZA_STATO_LABELS } from "../lib/copy";
import { formatDate, formatSlotGiorno, formatSlotOra } from "../lib/format";
import type { ConsulenzaStato } from "../types";

export function ConsulenzaStatoBadge({ stato }: { stato: ConsulenzaStato }) {
  const tone = stato === "assegnata" ? "emerald" : stato === "nuova" ? "amber" : "slate";
  return <Badge tone={tone}>{CONSULENZA_STATO_LABELS[stato]}</Badge>;
}

/** Le richieste di consulto dell'Azienda: pagina lista, dettaglio a parte. */
export default function Consulenze() {
  const { data: consulenze, isPending, isError, error, refetch } = useConsulenze();

  return (
    <div className="mx-auto max-w-4xl">
      <h1 className="font-display text-2xl font-bold tracking-tight text-slate-900">
        Consulenze
      </h1>
      <p className="mt-1 text-sm text-slate-500">
        Le tue richieste di consulto con i progettisti: dalle proposte ricevute
        all'appuntamento.
      </p>

      <div className="mt-6">
        {isPending ? (
          <div className="space-y-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-24 w-full" />
            ))}
          </div>
        ) : isError ? (
          <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
        ) : !consulenze || consulenze.length === 0 ? (
          <EmptyState
            title="Nessuna richiesta di consulto"
            description="Completa un AI-check su un bando e attiva il «Consulto esperto» dalla pagina del bando: la tua richiesta arriverà ai progettisti della piattaforma."
          />
        ) : (
          <div className="space-y-3">
            {consulenze.map((consulenza) => (
              <Link
                key={consulenza.id}
                to={`/app/consulenze/${consulenza.id}`}
                className="block rounded-2xl focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
              >
                <Card className="p-5 transition-shadow hover:shadow-md">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-medium text-slate-900">{consulenza.bando_titolo}</p>
                      <p className="mt-0.5 text-xs text-slate-400">
                        Richiesta del {formatDate(consulenza.created_at)}
                      </p>
                    </div>
                    <ConsulenzaStatoBadge stato={consulenza.stato} />
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-sm text-slate-600">
                    {consulenza.stato === "nuova" && (
                      <span className="inline-flex items-center gap-1.5">
                        <MessagesSquare className="size-4 text-slate-400" aria-hidden />
                        {consulenza.proposte_aperte === 0
                          ? "Nessuna proposta ricevuta finora"
                          : consulenza.proposte_aperte === 1
                            ? "1 proposta da valutare"
                            : `${consulenza.proposte_aperte} proposte da valutare`}
                      </span>
                    )}
                    {consulenza.progettista?.codice && (
                      <span>
                        Progettista{" "}
                        <span className="tabular font-medium text-slate-800">
                          {consulenza.progettista.codice}
                        </span>
                        {consulenza.progettista.nome && ` — ${consulenza.progettista.nome}`}
                      </span>
                    )}
                    {consulenza.appuntamento && (
                      <span className="inline-flex items-center gap-1.5">
                        <CalendarClock className="size-4 text-slate-400" aria-hidden />
                        {formatSlotGiorno(consulenza.appuntamento.inizio)},{" "}
                        {formatSlotOra(consulenza.appuntamento.inizio)}
                      </span>
                    )}
                  </div>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
