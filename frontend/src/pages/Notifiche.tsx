import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { Pagination } from "../components/ui/Pagination";
import { Select } from "../components/ui/Select";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { useActiveCompany } from "../hooks/useActiveCompany";
import { useMarkNotificationsRead, useNotificationsPage } from "../hooks/useNotifications";
import { apiErrorMessage } from "../lib/api";
import { cn } from "../lib/cn";
import { NOTIFICHE_COPY } from "../lib/copy";
import { formatDateTime } from "../lib/format";
import type { Notifica } from "../types";

/** Centro alert: tutte le notifiche in una pagina paginata, a righe. Per gli
 *  Advisor multi-azienda un filtro per azienda affianca la vista aggregata; il
 *  contatore della voce «Notifiche» (non lette) resta comunque su tutte le aziende. */
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
    <Page variante="elenco">
      <PageHeader
        titolo={NOTIFICHE_COPY.titoloPagina}
        descrizione={NOTIFICHE_COPY.sottotitoloPagina}
        azioni={
          (data?.non_lette ?? 0) > 0 && (
            <Button
              type="button"
              variant="secondary"
              loading={markRead.isPending}
              onClick={() => markRead.mutate({ all: true })}
            >
              {NOTIFICHE_COPY.segnaTutteLette}
            </Button>
          )
        }
      />

      {isMulti && companies.length > 0 && (
        <div>
          <Select
            label={NOTIFICHE_COPY.filtroAria}
            value={companyId ?? ""}
            onChange={(e) => setCompanyId(e.target.value || null)}
          >
            <option value="">{NOTIFICHE_COPY.filtroTutte}</option>
            {companies.map((c) => (
              <option key={c.id} value={c.id}>
                {c.ragione_sociale}
              </option>
            ))}
          </Select>
        </div>
      )}

      <section aria-busy={isPending || isPlaceholderData} className="flex flex-col gap-8">
        {isPending ? (
          <div className="flex flex-col gap-3" aria-hidden>
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-16 w-full" />
            ))}
          </div>
        ) : isError ? (
          <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
        ) : data && data.items.length === 0 ? (
          companyId ? (
            <EmptyState
              title={NOTIFICHE_COPY.vuotoAzienda}
              action={
                <Button type="button" variant="secondary" onClick={() => setCompanyId(null)}>
                  Mostra tutte le aziende
                </Button>
              }
            />
          ) : (
            <EmptyState title={NOTIFICHE_COPY.vuoto} />
          )
        ) : (
          <>
            <ul
              className={cn(
                "flex flex-col border-t border-line",
                isPlaceholderData && "opacity-60 transition-opacity",
              )}
            >
              {data?.items.map((notifica) => {
                const nonLetta = !notifica.read_at;
                const nomeAzienda = notifica.company_profile_id
                  ? nomiAziende.get(notifica.company_profile_id)
                  : undefined;
                return (
                  <li key={notifica.id} className="border-b border-line">
                    <button
                      type="button"
                      onClick={() => handleItemClick(notifica)}
                      className={cn(
                        "flex w-full items-start gap-3 px-2 py-4 text-left transition-colors",
                        "hover:bg-desk",
                        "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent",
                        notifica.url ? "cursor-pointer" : "cursor-default",
                      )}
                    >
                      {/* Punto delle non lette (le lette non hanno segno): solo
                          visivo, a parole lo dice il prefisso sr-only. */}
                      <span
                        aria-hidden
                        className={cn(
                          "mt-2 size-2 shrink-0 rounded-pill",
                          nonLetta && "bg-accent",
                        )}
                      />
                      <span className="flex min-w-0 flex-1 flex-col gap-1">
                        <span className="flex flex-wrap items-center gap-2">
                          <span
                            className={cn(
                              "text-body text-ink",
                              nonLetta ? "font-semibold" : "font-normal",
                            )}
                          >
                            {nonLetta && (
                              <span className="sr-only">{NOTIFICHE_COPY.nonLetta} </span>
                            )}
                            {notifica.titolo}
                          </span>
                          {nomeAzienda && <Badge>{nomeAzienda}</Badge>}
                        </span>
                        {notifica.corpo && (
                          <span className="block text-body text-ink-2">{notifica.corpo}</span>
                        )}
                        <span className="block text-caption text-ink-3 tabular-nums">
                          {formatDateTime(notifica.created_at)}
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
            <Pagination
              page={page}
              totalPages={data?.total_pages ?? 1}
              onChange={(next) => {
                setPage(next);
                window.scrollTo({ top: 0, behavior: "smooth" });
              }}
            />
          </>
        )}
      </section>
    </Page>
  );
}
