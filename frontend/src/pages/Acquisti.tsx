import { FileDown, ShieldCheck, ShoppingBag } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, type BadgeProps } from "../components/ui/Badge";
import { LinkButton } from "../components/ui/Button";
import { Pagination } from "../components/ui/Pagination";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { usePurchases } from "../hooks/useCheckout";
import { apiErrorMessage } from "../lib/api";
import { cn } from "../lib/cn";
import { PURCHASE_KIND_LABELS, PURCHASE_STATO_LABELS } from "../lib/copy";
import { downloadFile } from "../lib/download";
import { eurFromCents, formatDateTime } from "../lib/format";
import type { PurchaseStatus } from "../types";

/** Scarica il documento (copia di cortesia; l'originale fiscale è la fattura
 *  elettronica trasmessa a SDI). Disponibile solo sugli acquisti pagati. */
function ScaricaDocumento({ purchaseId }: { purchaseId: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="mt-1">
      <button
        type="button"
        disabled={busy}
        onClick={async () => {
          setError(null);
          setBusy(true);
          try {
            await downloadFile(
              `/me/purchases/${purchaseId}/documento.pdf`,
              `documento-${purchaseId.slice(0, 8)}.pdf`,
            );
          } catch (e) {
            setError(e instanceof Error ? e.message : "Download non riuscito. Riprova.");
          } finally {
            setBusy(false);
          }
        }}
        className="inline-flex items-center gap-1 text-sm font-medium text-brand-600 hover:text-brand-700 disabled:opacity-60"
      >
        <FileDown className="size-3.5" aria-hidden />
        {busy ? "Preparazione…" : "Scarica documento"}
      </button>
      {error && (
        <span className="ml-2 text-xs text-red-600" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

const STATO_TONI: Record<PurchaseStatus, BadgeProps["tone"]> = {
  in_attesa: "amber",
  pagato: "emerald",
  fallito: "red",
  scaduto: "slate",
  annullato: "slate",
  gratuito: "slate",
};

export default function Acquisti() {
  const [page, setPage] = useState(1);
  const { data, isPending, isError, error, refetch, isPlaceholderData } = usePurchases(page);

  // Su una pagina > 1 rimasta vuota (totale calato) si rientra sull'ultima.
  useEffect(() => {
    if (data && page > 1 && data.items.length === 0 && data.total > 0) {
      setPage(Math.max(1, data.total_pages));
    }
  }, [data, page]);

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="inline-flex items-center gap-2 font-display text-2xl font-bold tracking-tight text-slate-900">
        <ShoppingBag className="size-6 text-brand-500" aria-hidden />
        I tuoi acquisti
      </h1>
      <p className="mt-1 text-sm text-slate-500">
        Lo storico di piani e add-on acquistati su BandoFit.
      </p>

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
            title="Nessun acquisto ancora"
            description="Quando acquisti un piano o un add-on lo trovi qui, con il suo stato."
            action={<LinkButton to="/app/abbonamento">Vedi i piani</LinkButton>}
          />
        ) : (
          <>
            <ul className={cn("space-y-2", isPlaceholderData && "opacity-60 transition-opacity")}>
              {data?.items.map((p) => {
                return (
                  <li
                    key={p.id}
                    className="flex items-start justify-between gap-4 rounded-xl border border-slate-200 bg-white p-4"
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-sm font-medium text-slate-900">{p.descrizione}</p>
                        <Badge tone={STATO_TONI[p.status]}>{PURCHASE_STATO_LABELS[p.status]}</Badge>
                        {(p.kind === "cambio_admin" || p.kind === "addon_admin") && (
                          <Badge tone="brand">
                            <ShieldCheck className="size-3" aria-hidden />
                            {PURCHASE_KIND_LABELS[p.kind]}
                          </Badge>
                        )}
                      </div>
                      {(p.kind === "cambio_admin" || p.kind === "addon_admin") &&
                        p.motivazione && (
                          <p className="mt-1 text-sm text-slate-500">{p.motivazione}</p>
                        )}
                      <p className="mt-1 text-xs text-slate-400">
                        {formatDateTime(p.created_at)}
                      </p>
                      {p.status === "in_attesa" && (
                        <Link
                          to={`/app/checkout/esito/${p.id}`}
                          className="mt-1 inline-block text-sm font-medium text-brand-600 hover:text-brand-700"
                        >
                          Verifica lo stato →
                        </Link>
                      )}
                      {p.status === "pagato" && <ScaricaDocumento purchaseId={p.id} />}
                    </div>
                    <p className="shrink-0 text-sm font-semibold tabular-nums text-slate-900">
                      {eurFromCents(p.totale_cents)}
                    </p>
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
