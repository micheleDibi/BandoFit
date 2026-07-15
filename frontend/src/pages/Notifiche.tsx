import { Bell, Building2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Pagination } from "../components/ui/Pagination";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { useActiveCompany } from "../hooks/useActiveCompany";
import { useMarkNotificationsRead, useNotificationsPage } from "../hooks/useNotifications";
import { apiErrorMessage } from "../lib/api";
import { cn } from "../lib/cn";
import { NOTIFICHE_COPY } from "../lib/copy";
import { formatDateTime } from "../lib/format";
import type { Notifica } from "../types";

/** Centro alert: tutte le notifiche in una pagina paginata. Per gli Advisor
 *  multi-azienda un filtro per azienda affianca la vista aggregata; il badge
 *  della campanella (conteggio non-lette) resta comunque su tutte le aziende. */
export default function Notifiche() {
  const navigate = useNavigate();
  const { isMulti, companies } = useActiveCompany();
  const [companyId, setCompanyId] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const { data, isPending, isError, error, refetch, isPlaceholderData } = useNotificationsPage(
    page,
    companyId,
  );
  const markRead = useMarkNotificationsRead();

  // Cambiando filtro si riparte da pagina 1 (l'intervallo cambia).
  useEffect(() => {
    setPage(1);
  }, [companyId]);

  // Rimuovendo l'ultimo elemento di una pagina > 1 si rientra sull'ultima piena.
  useEffect(() => {
    if (data && page > 1 && data.items.length === 0 && data.total > 0) {
      setPage(Math.max(1, data.total_pages));
    }
  }, [data, page]);

  const nomiAziende = useMemo(
    () => new Map(companies.map((c) => [c.id, c.ragione_sociale])),
    [companies],
  );

  const handleItemClick = (notifica: Notifica) => {
    if (!notifica.read_at) markRead.mutate({ ids: [notifica.id] });
    if (notifica.url) navigate(notifica.url);
  };

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="inline-flex items-center gap-2 font-display text-2xl font-bold tracking-tight text-slate-900">
            <Bell className="size-6 text-brand-500" aria-hidden />
            {NOTIFICHE_COPY.titoloPagina}
          </h1>
          <p className="mt-1 text-sm text-slate-500">{NOTIFICHE_COPY.sottotitoloPagina}</p>
        </div>
        {(data?.non_lette ?? 0) > 0 && (
          <Button
            variant="secondary"
            size="sm"
            loading={markRead.isPending}
            onClick={() => markRead.mutate({ all: true })}
          >
            {NOTIFICHE_COPY.segnaTutteLette}
          </Button>
        )}
      </div>

      {isMulti && companies.length > 0 && (
        <div className="mt-5 flex items-center gap-2">
          <Building2 className="size-4 shrink-0 text-slate-400" aria-hidden />
          <select
            aria-label={NOTIFICHE_COPY.filtroAria}
            value={companyId ?? ""}
            onChange={(e) => setCompanyId(e.target.value || null)}
            className="h-9 cursor-pointer rounded-lg border border-slate-300 bg-white px-3 text-sm font-medium text-slate-700 transition-colors hover:border-brand-400 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
          >
            <option value="">{NOTIFICHE_COPY.filtroTutte}</option>
            {companies.map((c) => (
              <option key={c.id} value={c.id}>
                {c.ragione_sociale}
              </option>
            ))}
          </select>
        </div>
      )}

      <section className="mt-6" aria-busy={isPending || isPlaceholderData}>
        {isPending ? (
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-20 w-full rounded-xl" />
            ))}
          </div>
        ) : isError ? (
          <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
        ) : data && data.items.length === 0 ? (
          <EmptyState
            title={NOTIFICHE_COPY.titoloPagina}
            description={companyId ? NOTIFICHE_COPY.vuotoAzienda : NOTIFICHE_COPY.vuoto}
          />
        ) : (
          <>
            <ul
              className={cn(
                "space-y-2",
                isPlaceholderData && "opacity-60 transition-opacity",
              )}
            >
              {data?.items.map((notifica) => {
                const nomeAzienda = notifica.company_profile_id
                  ? nomiAziende.get(notifica.company_profile_id)
                  : undefined;
                return (
                  <li key={notifica.id}>
                    <button
                      type="button"
                      onClick={() => handleItemClick(notifica)}
                      className={cn(
                        "flex w-full items-start gap-3 rounded-xl border p-4 text-left transition-colors",
                        notifica.read_at
                          ? "border-slate-200 bg-white hover:bg-slate-50"
                          : "border-brand-200 bg-brand-50/50 hover:bg-brand-50",
                        !notifica.url && "cursor-default",
                      )}
                    >
                      <span
                        aria-hidden
                        className={cn(
                          "mt-1.5 size-2 shrink-0 rounded-full",
                          notifica.read_at ? "bg-transparent" : "bg-brand-500",
                        )}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-center gap-2">
                          <span
                            className={cn(
                              "text-sm",
                              notifica.read_at
                                ? "text-slate-600"
                                : "font-semibold text-slate-900",
                            )}
                          >
                            {notifica.titolo}
                          </span>
                          {nomeAzienda && (
                            <Badge tone="brand">
                              <Building2 className="size-3" aria-hidden />
                              {nomeAzienda}
                            </Badge>
                          )}
                        </span>
                        {notifica.corpo && (
                          <span className="mt-1 block text-sm text-slate-500">
                            {notifica.corpo}
                          </span>
                        )}
                        <span className="tabular mt-1 block text-xs text-slate-400">
                          {formatDateTime(notifica.created_at)}
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
            <div className="mt-8">
              <Pagination
                page={page}
                totalPages={data?.total_pages ?? 1}
                onChange={(next) => {
                  setPage(next);
                  window.scrollTo({ top: 0, behavior: "smooth" });
                }}
              />
            </div>
          </>
        )}
      </section>
    </div>
  );
}
