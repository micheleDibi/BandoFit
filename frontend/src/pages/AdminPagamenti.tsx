import { AlertTriangle, CreditCard, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, type BadgeProps } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Pagination } from "../components/ui/Pagination";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import {
  useAdminAnomalies,
  useAdminInvoices,
  useAdminPurchases,
  useResolveAnomaly,
  useRetryInvoice,
} from "../hooks/useAdmin";
import { apiErrorMessage } from "../lib/api";
import { cn } from "../lib/cn";
import { PURCHASE_STATO_LABELS } from "../lib/copy";
import { eurFromCents, formatDateNumeric, formatDateTime } from "../lib/format";
import type { AdminInvoice, InvoiceStato, PurchaseKind, PurchaseStatus } from "../types";

const PURCHASE_TONI: Record<PurchaseStatus, BadgeProps["tone"]> = {
  in_attesa: "amber",
  pagato: "emerald",
  fallito: "red",
  scaduto: "slate",
  annullato: "slate",
  gratuito: "slate",
};

const KIND_LABELS: Record<PurchaseKind, string> = {
  piano: "Piano",
  rinnovo: "Rinnovo",
  addon: "Add-on",
  cambio_admin: "Cambio amministratore",
};

/** Colori sobri per lo stato SDI: verde solo a consegna avvenuta, rosso dove
 *  serve un intervento (scarto/errore), amber per tutto il percorso normale. */
const INVOICE_STATI: Record<InvoiceStato, { label: string; tone: BadgeProps["tone"] }> = {
  da_emettere: { label: "Da emettere", tone: "amber" },
  in_invio: { label: "In invio", tone: "amber" },
  inviata: { label: "Inviata", tone: "amber" },
  consegnata: { label: "Consegnata", tone: "emerald" },
  non_consegnata: { label: "Non consegnata", tone: "amber" },
  scartata: { label: "Scartata", tone: "red" },
  errore: { label: "Errore", tone: "red" },
};

/** «12/2026» (o «A12/2026» con serie); il numero arriva solo al primo invio. */
const numeroFattura = (inv: AdminInvoice) =>
  inv.numero !== null ? `${inv.serie}${inv.numero}/${inv.anno}` : "—";

const thClass = "px-4 py-3 font-medium";
const selectClass =
  "h-11 cursor-pointer rounded-xl border border-slate-300 bg-white px-3 text-sm shadow-card focus:border-brand-500 focus:outline-none";

function SezioneAcquisti() {
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [status, kind]);

  const { data, isPending, isError, error, refetch, isPlaceholderData } = useAdminPurchases({
    status,
    kind,
    page,
  });

  return (
    <>
      <div className="mt-5 flex flex-wrap gap-3">
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          aria-label="Filtra per stato"
          className={selectClass}
        >
          <option value="">Tutti gli stati</option>
          {(Object.keys(PURCHASE_STATO_LABELS) as PurchaseStatus[]).map((s) => (
            <option key={s} value={s}>
              {PURCHASE_STATO_LABELS[s]}
            </option>
          ))}
        </select>
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          aria-label="Filtra per tipo"
          className={selectClass}
        >
          <option value="">Tutti i tipi</option>
          {(Object.keys(KIND_LABELS) as PurchaseKind[]).map((k) => (
            <option key={k} value={k}>
              {KIND_LABELS[k]}
            </option>
          ))}
        </select>
      </div>

      <Card className="mt-5 overflow-hidden" aria-busy={isPending || isPlaceholderData}>
        {isPending ? (
          <div className="space-y-3 p-5">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : isError ? (
          <div className="p-5">
            <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
          </div>
        ) : data && data.items.length === 0 ? (
          <div className="p-5">
            <EmptyState title="Nessun acquisto" description="Con questi filtri non c'è nulla." />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table
              className={cn(
                "w-full min-w-[720px] text-left text-sm",
                isPlaceholderData && "opacity-60 transition-opacity",
              )}
            >
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50/70 text-xs uppercase tracking-wide text-slate-500">
                  <th className={thClass}>Data</th>
                  <th className={thClass}>Descrizione</th>
                  <th className={cn(thClass, "text-right")}>Totale</th>
                  <th className={thClass}>Stato</th>
                </tr>
              </thead>
              <tbody>
                {data?.items.map((p) => (
                  <tr
                    key={p.id}
                    className="border-b border-slate-100 last:border-b-0 hover:bg-slate-50/60"
                  >
                    <td className="tabular whitespace-nowrap px-4 py-3 text-slate-500">
                      {formatDateTime(p.created_at)}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-slate-900">{p.descrizione}</span>
                        {p.kind === "cambio_admin" && (
                          <Badge tone="brand">
                            <ShieldCheck className="size-3" aria-hidden />
                            Cambio admin
                          </Badge>
                        )}
                      </div>
                      {p.kind === "cambio_admin" && p.motivazione && (
                        <p className="mt-0.5 text-xs text-slate-500">{p.motivazione}</p>
                      )}
                      {p.decline_reason && (
                        <p className="mt-0.5 text-xs text-slate-400">
                          Declino: {p.decline_reason}
                        </p>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right font-medium tabular-nums text-slate-900">
                      {eurFromCents(p.totale_cents)}
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={PURCHASE_TONI[p.status]}>
                        {PURCHASE_STATO_LABELS[p.status]}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {data && data.total_pages > 1 && (
        <div className="mt-6">
          <Pagination page={page} totalPages={data.total_pages} onChange={setPage} />
        </div>
      )}
    </>
  );
}

function SezioneFatture() {
  const [stato, setStato] = useState("");
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [stato]);

  const { data, isPending, isError, error, refetch, isPlaceholderData } = useAdminInvoices({
    stato,
    page,
  });
  const retry = useRetryInvoice();
  const [esitoRetry, setEsitoRetry] = useState<string | null>(null);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  const handleRetry = async (inv: AdminInvoice) => {
    setEsitoRetry(null);
    try {
      const esito = await retry.mutateAsync(inv.id);
      setEsitoRetry(
        esito.note ?? `Ritrasmissione di ${numeroFattura(inv)} avviata: stato «${INVOICE_STATI[esito.stato as InvoiceStato]?.label ?? esito.stato}».`,
      );
    } catch (err) {
      setEsitoRetry(apiErrorMessage(err));
    }
  };

  return (
    <>
      <div className="mt-5 flex flex-wrap gap-3">
        <select
          value={stato}
          onChange={(e) => setStato(e.target.value)}
          aria-label="Filtra per stato SDI"
          className={selectClass}
        >
          <option value="">Tutti gli stati</option>
          {(Object.keys(INVOICE_STATI) as InvoiceStato[]).map((s) => (
            <option key={s} value={s}>
              {INVOICE_STATI[s].label}
            </option>
          ))}
        </select>
      </div>

      {esitoRetry && (
        <p className="mt-3 rounded-lg bg-brand-50 px-4 py-3 text-sm text-brand-800" role="status">
          {esitoRetry}
        </p>
      )}

      <Card className="mt-5 overflow-hidden" aria-busy={isPending || isPlaceholderData}>
        {isPending ? (
          <div className="space-y-3 p-5">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : isError ? (
          <div className="p-5">
            <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
          </div>
        ) : data && data.items.length === 0 ? (
          <div className="p-5">
            <EmptyState title="Nessuna fattura" description="Con questo filtro non c'è nulla." />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table
              className={cn(
                "w-full min-w-[760px] text-left text-sm",
                isPlaceholderData && "opacity-60 transition-opacity",
              )}
            >
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50/70 text-xs uppercase tracking-wide text-slate-500">
                  <th className={thClass}>Numero</th>
                  <th className={thClass}>Data documento</th>
                  <th className={cn(thClass, "text-right")}>Totale</th>
                  <th className={thClass}>Stato SDI</th>
                  <th className={cn(thClass, "text-right")}>Tentativi</th>
                  <th className={cn(thClass, "text-right")}>Azioni</th>
                </tr>
              </thead>
              <tbody>
                {data?.items.map((inv) => {
                  const stile = INVOICE_STATI[inv.stato];
                  const ritrasmettibile = inv.stato === "errore" || inv.stato === "scartata";
                  return (
                    <tr
                      key={inv.id}
                      className="border-b border-slate-100 last:border-b-0 hover:bg-slate-50/60"
                    >
                      <td className="tabular px-4 py-3 font-medium text-slate-900">
                        {numeroFattura(inv)}
                      </td>
                      <td className="tabular px-4 py-3 text-slate-500">
                        {formatDateNumeric(inv.data_documento)}
                      </td>
                      <td className="px-4 py-3 text-right font-medium tabular-nums text-slate-900">
                        {eurFromCents(inv.totale_cents)}
                      </td>
                      <td className="px-4 py-3">
                        <Badge tone={stile.tone}>{stile.label}</Badge>
                      </td>
                      <td className="tabular px-4 py-3 text-right text-slate-500">
                        {inv.tentativi}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {ritrasmettibile && (
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => handleRetry(inv)}
                            loading={retry.isPending && retry.variables === inv.id}
                            disabled={retry.isPending}
                          >
                            Ritrasmetti
                          </Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {data && totalPages > 1 && (
        <div className="mt-6">
          <Pagination page={page} totalPages={totalPages} onChange={setPage} />
        </div>
      )}
    </>
  );
}

function SezioneAnomalie() {
  const [stato, setStato] = useState<"aperta" | "risolta">("aperta");
  const { data, isPending, isError, error, refetch } = useAdminAnomalies(stato);
  const resolve = useResolveAnomaly();
  const [resolveError, setResolveError] = useState<string | null>(null);

  const handleResolve = async (auditId: number) => {
    setResolveError(null);
    try {
      await resolve.mutateAsync(auditId);
    } catch (err) {
      setResolveError(apiErrorMessage(err));
    }
  };

  return (
    <>
      <div className="mt-5 flex gap-2" role="group" aria-label="Filtra le anomalie">
        {(["aperta", "risolta"] as const).map((s) => (
          <button
            key={s}
            type="button"
            aria-pressed={stato === s}
            onClick={() => setStato(s)}
            className={cn(
              "cursor-pointer rounded-lg px-3 py-2 text-sm font-medium transition-colors",
              stato === s
                ? "bg-brand-50 text-brand-700 ring-1 ring-inset ring-brand-200"
                : "text-slate-600 hover:bg-slate-100",
            )}
          >
            {s === "aperta" ? "Aperte" : "Risolte"}
          </button>
        ))}
      </div>

      {resolveError && (
        <p className="mt-3 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">
          {resolveError}
        </p>
      )}

      <div className="mt-5">
        {isPending ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-20 w-full rounded-xl" />
            ))}
          </div>
        ) : isError ? (
          <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
        ) : (data?.items.length ?? 0) === 0 ? (
          <EmptyState
            title={stato === "aperta" ? "Nessuna anomalia aperta" : "Nessuna anomalia risolta"}
            description={
              stato === "aperta"
                ? "Tutti gli incassi corrispondono a un acquisto: niente da riconciliare."
                : "Le anomalie risolte compariranno qui."
            }
          />
        ) : (
          <ul className="space-y-2">
            {data?.items.map((a) => (
              <li
                key={a.audit_id}
                className={cn(
                  "flex flex-wrap items-center justify-between gap-3 rounded-xl border p-4",
                  a.risolta ? "border-slate-200 bg-white" : "border-red-200 bg-red-50/50",
                )}
              >
                <div className="min-w-0">
                  <p className="inline-flex items-center gap-1.5 text-sm font-medium text-slate-900">
                    <AlertTriangle
                      className={cn("size-4 shrink-0", a.risolta ? "text-slate-400" : "text-red-500")}
                      aria-hidden
                    />
                    {a.payload?.motivo ?? "Incasso da riconciliare"}
                  </p>
                  <p className="tabular mt-1 text-xs text-slate-500">
                    Ordine Revolut: {a.payload?.revolut_order_id ?? "—"}
                    {a.payload?.purchase_id && <> · Purchase: {a.payload.purchase_id}</>}
                  </p>
                  <p className="mt-0.5 text-xs text-slate-400">{formatDateTime(a.created_at)}</p>
                </div>
                {!a.risolta && (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => handleResolve(a.audit_id)}
                    loading={resolve.isPending && resolve.variables === a.audit_id}
                    disabled={resolve.isPending}
                  >
                    Segna come risolta
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}

type Tab = "acquisti" | "fatture" | "anomalie";

const TABS: Array<{ id: Tab; label: string }> = [
  { id: "acquisti", label: "Acquisti" },
  { id: "fatture", label: "Fatture" },
  { id: "anomalie", label: "Anomalie" },
];

export default function AdminPagamenti() {
  const [tab, setTab] = useState<Tab>("acquisti");
  // Le anomalie aperte pesano sempre sul banner, qualunque sia il tab attivo
  // (stessa query della sezione: la cache è condivisa).
  const { data: aperte } = useAdminAnomalies("aperta");
  const numAperte = aperte?.items.length ?? 0;

  return (
    <div>
      <h1 className="inline-flex items-center gap-2 font-display text-2xl font-bold tracking-tight text-slate-900">
        <CreditCard className="size-6 text-brand-500" aria-hidden />
        Pagamenti
      </h1>
      <p className="mt-1 text-sm text-slate-500">
        Storico acquisti, fatture elettroniche e incassi da riconciliare.
      </p>

      {numAperte > 0 && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3">
          <p className="inline-flex items-center gap-2 text-sm font-medium text-red-700">
            <AlertTriangle className="size-4 shrink-0" aria-hidden />
            {numAperte === 1
              ? "1 incasso da riconciliare"
              : `${numAperte} incassi da riconciliare`}
          </p>
          {tab !== "anomalie" && (
            <Button variant="secondary" size="sm" onClick={() => setTab("anomalie")}>
              Vedi le anomalie
            </Button>
          )}
        </div>
      )}

      <div className="mt-5 flex gap-1 border-b border-slate-200" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              "-mb-px cursor-pointer border-b-2 px-4 py-2.5 text-sm font-medium transition-colors",
              tab === t.id
                ? "border-brand-500 text-brand-700"
                : "border-transparent text-slate-500 hover:text-slate-800",
            )}
          >
            {t.label}
            {t.id === "anomalie" && numAperte > 0 && (
              <span className="ml-1.5 inline-flex min-w-5 items-center justify-center rounded-full bg-red-100 px-1.5 text-xs font-semibold text-red-700">
                {numAperte}
              </span>
            )}
          </button>
        ))}
      </div>

      {tab === "acquisti" && <SezioneAcquisti />}
      {tab === "fatture" && <SezioneFatture />}
      {tab === "anomalie" && <SezioneAnomalie />}
    </div>
  );
}
