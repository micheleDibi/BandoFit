import { CircleCheck, CreditCard, Receipt, ShieldCheck } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Alert } from "../components/ui/Alert";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { Pagination } from "../components/ui/Pagination";
import { Segment } from "../components/ui/Segment";
import { Select } from "../components/ui/Select";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { Status, type TonoStatus } from "../components/ui/Status";
import { Table, Td, Th } from "../components/ui/Table";
import { TabPanel, Tabs } from "../components/ui/Tabs";
import {
  useAdminAnomalies,
  useAdminInvoices,
  useAdminPurchases,
  useResolveAnomaly,
} from "../hooks/useAdmin";
import { useTab } from "../hooks/useTab";
import { apiErrorMessage } from "../lib/api";
import { cn } from "../lib/cn";
import { PURCHASE_KIND_LABELS, PURCHASE_STATO_LABELS } from "../lib/copy";
import { eurFromCents, formatDateNumeric, formatDateTime } from "../lib/format";
import type { AdminInvoice, InvoiceStato, PurchaseKind, PurchaseStatus } from "../types";

/** Stato dell'acquisto in parole (`Status`): il tono dice solo l'esito. Gli
 *  esiti negativi usano `errore` (rosso): `attenzione` ora è ambra. */
const PURCHASE_TONI: Record<PurchaseStatus, TonoStatus> = {
  in_attesa: "in-apertura",
  pagato: "aperto",
  fallito: "errore",
  scaduto: "chiuso",
  annullato: "chiuso",
  gratuito: "neutro",
};

/** Stati del registro fatture. Le nuove righe restano «Da emettere» (l'emissione
 *  è fuori piattaforma); gli altri stati sono storici, di quando la piattaforma
 *  trasmetteva a SDI, e restano leggibili sulle righe di allora. */
const INVOICE_STATI: Record<InvoiceStato, { label: string; tono: TonoStatus }> = {
  da_emettere: { label: "Da emettere", tono: "in-apertura" },
  in_invio: { label: "In invio", tono: "in-apertura" },
  inviata: { label: "Inviata", tono: "in-apertura" },
  consegnata: { label: "Consegnata", tono: "aperto" },
  non_consegnata: { label: "Non consegnata", tono: "attenzione" },
  scartata: { label: "Scartata", tono: "errore" },
  errore: { label: "Errore", tono: "errore" },
};

/** «12/2026» (o «A12/2026» con serie); presente solo sulle righe storiche già
 *  trasmesse: le nuove non ricevono un numero dalla piattaforma. */
const numeroFattura = (inv: AdminInvoice) =>
  inv.numero !== null ? `${inv.serie}${inv.numero}/${inv.anno}` : "—";

function SkeletonRighe({ n = 6 }: { n?: number }) {
  return (
    <div className="flex flex-col gap-3" aria-hidden>
      {Array.from({ length: n }).map((_, i) => (
        <Skeleton key={i} className="h-12 w-full rounded-control" />
      ))}
    </div>
  );
}

function SezioneAcquisti() {
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const [page, setPage] = useState(1);

  const { data, isPending, isError, error, refetch, isPlaceholderData } = useAdminPurchases({
    status,
    kind,
    page,
  });

  // Pagina oltre l'ultima: si rientra sull'ultima piena invece di mostrare il
  // vuoto. Mai sui dati segnaposto della query precedente. (Il ritorno a
  // pagina 1 al cambio di filtro sta negli `onChange`: niente corsa con questo.)
  const fuoriPagina =
    !!data && page > 1 && page > data.total_pages && data.items.length === 0 && data.total > 0;
  useEffect(() => {
    if (fuoriPagina && !isPlaceholderData && data) setPage(Math.max(1, data.total_pages));
  }, [fuoriPagina, isPlaceholderData, data]);

  let elenco: ReactNode;
  if (isPending) {
    elenco = <SkeletonRighe />;
  } else if (isError) {
    elenco = <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />;
  } else if (fuoriPagina) {
    elenco = <SkeletonRighe />;
  } else if (data && data.items.length === 0) {
    elenco = (
      <Card>
        <EmptyState
          title="Nessun acquisto"
          description="Con questi filtri non c'è nulla."
          icon={CreditCard}
          area="admin"
        />
      </Card>
    );
  } else {
    elenco = (
      <Card className="overflow-hidden p-0">
        <Table
          className={cn(
            "min-w-[720px] [&_tbody_tr:last-child_td]:border-b-0",
            isPlaceholderData && "opacity-60 transition-opacity",
          )}
          classNameContenitore="px-2"
        >
          <caption className="sr-only">Storico degli acquisti</caption>
          <thead>
            <tr>
              <Th>Data</Th>
              <Th>Descrizione</Th>
              <Th numerica>Totale</Th>
              <Th>Stato</Th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((p) => (
              <tr key={p.id} className="transition-colors duration-150 ease-uscita hover:bg-desk">
                <Td className="whitespace-nowrap text-ink-2 tabular-nums">
                  {formatDateTime(p.created_at)}
                </Td>
                <Td>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-ink">{p.descrizione}</span>
                    {p.kind === "cambio_admin" && (
                      <Badge>
                        <ShieldCheck className="size-3.5" aria-hidden />
                        Cambio admin
                      </Badge>
                    )}
                    {p.kind === "addon_admin" && (
                      <Badge>
                        <ShieldCheck className="size-3.5" aria-hidden />
                        {PURCHASE_KIND_LABELS.addon_admin}
                      </Badge>
                    )}
                  </div>
                  {(p.kind === "cambio_admin" || p.kind === "addon_admin") && p.motivazione && (
                    <p className="text-small text-ink-2">{p.motivazione}</p>
                  )}
                  {p.decline_reason && (
                    <p className="text-small text-ink-3">Declino: {p.decline_reason}</p>
                  )}
                </Td>
                <Td numerica className="font-medium text-ink">
                  {eurFromCents(p.totale_cents)}
                </Td>
                <Td>
                  <Status tono={PURCHASE_TONI[p.status]}>{PURCHASE_STATO_LABELS[p.status]}</Status>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>
    );
  }

  return (
    <>
      <div className="flex flex-wrap gap-3">
        <Select
          label="Filtra per stato"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Tutti gli stati</option>
          {(Object.keys(PURCHASE_STATO_LABELS) as PurchaseStatus[]).map((s) => (
            <option key={s} value={s}>
              {PURCHASE_STATO_LABELS[s]}
            </option>
          ))}
        </Select>
        <Select
          label="Filtra per tipo"
          value={kind}
          onChange={(e) => {
            setKind(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Tutti i tipi</option>
          {(Object.keys(PURCHASE_KIND_LABELS) as PurchaseKind[]).map((k) => (
            <option key={k} value={k}>
              {PURCHASE_KIND_LABELS[k]}
            </option>
          ))}
        </Select>
      </div>

      <div aria-busy={isPending || isPlaceholderData || fuoriPagina}>{elenco}</div>

      {data && data.total_pages > 1 && (
        <Pagination page={page} totalPages={data.total_pages} onChange={setPage} />
      )}
    </>
  );
}

function SezioneFatture() {
  const [stato, setStato] = useState("");
  const [page, setPage] = useState(1);

  const { data, isPending, isError, error, refetch, isPlaceholderData } = useAdminInvoices({
    stato,
    page,
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  // Pagina oltre l'ultima: si rientra sull'ultima piena (le fatture non hanno
  // `total_pages`: si calcola qui sopra). Il ritorno a pagina 1 al cambio di
  // filtro sta nell'`onChange`.
  const fuoriPagina =
    !!data && page > 1 && page > totalPages && data.items.length === 0 && data.total > 0;
  useEffect(() => {
    if (fuoriPagina && !isPlaceholderData) setPage(totalPages);
  }, [fuoriPagina, isPlaceholderData, totalPages]);

  let elenco: ReactNode;
  if (isPending) {
    elenco = <SkeletonRighe />;
  } else if (isError) {
    elenco = <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />;
  } else if (fuoriPagina) {
    elenco = <SkeletonRighe />;
  } else if (data && data.items.length === 0) {
    elenco = (
      <Card>
        <EmptyState
          title="Nessuna fattura"
          description="Con questo filtro non c'è nulla."
          icon={Receipt}
          area="admin"
        />
      </Card>
    );
  } else {
    elenco = (
      <Card className="overflow-hidden p-0">
        <Table
          className={cn(
            "min-w-[760px] [&_tbody_tr:last-child_td]:border-b-0",
            isPlaceholderData && "opacity-60 transition-opacity",
          )}
          classNameContenitore="px-2"
        >
          <caption className="sr-only">Registro delle fatture</caption>
          <thead>
            <tr>
              <Th>Numero</Th>
              <Th>Data documento</Th>
              <Th numerica>Totale</Th>
              <Th>Stato</Th>
              <Th numerica>Tentativi</Th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((inv) => {
              const stile = INVOICE_STATI[inv.stato];
              return (
                <tr key={inv.id} className="transition-colors duration-150 ease-uscita hover:bg-desk">
                  <Td className="font-medium text-ink tabular-nums">{numeroFattura(inv)}</Td>
                  <Td className="text-ink-2 tabular-nums">{formatDateNumeric(inv.data_documento)}</Td>
                  <Td numerica className="font-medium text-ink">
                    {eurFromCents(inv.totale_cents)}
                  </Td>
                  <Td>
                    <Status tono={stile.tono}>{stile.label}</Status>
                  </Td>
                  <Td numerica className="text-ink-2">
                    {inv.tentativi}
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </Card>
    );
  }

  return (
    <>
      <div className="flex flex-wrap gap-3">
        <Select
          label="Filtra per stato"
          value={stato}
          onChange={(e) => {
            setStato(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Tutti gli stati</option>
          {(Object.keys(INVOICE_STATI) as InvoiceStato[]).map((s) => (
            <option key={s} value={s}>
              {INVOICE_STATI[s].label}
            </option>
          ))}
        </Select>
      </div>

      <div aria-busy={isPending || isPlaceholderData || fuoriPagina}>{elenco}</div>

      {data && totalPages > 1 && (
        <Pagination page={page} totalPages={totalPages} onChange={setPage} />
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
      <Segment
        ariaLabel="Filtra le anomalie"
        opzioni={[
          { id: "aperta", label: "Aperte" },
          { id: "risolta", label: "Risolte" },
        ]}
        valore={stato}
        onChange={setStato}
        className="self-start"
      />

      {resolveError && <Alert tono="errore">{resolveError}</Alert>}

      {isPending ? (
        <SkeletonRighe n={3} />
      ) : isError ? (
        <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
      ) : (data?.items.length ?? 0) === 0 ? (
        <Card>
          <EmptyState
            title={stato === "aperta" ? "Nessuna anomalia aperta" : "Nessuna anomalia risolta"}
            description={
              stato === "aperta"
                ? "Tutti gli incassi corrispondono a un acquisto: niente da riconciliare."
                : "Le anomalie risolte compariranno qui."
            }
            icon={CircleCheck}
            area="admin"
          />
        </Card>
      ) : (
        <Card className="overflow-hidden p-0">
          <ul className="flex flex-col divide-y divide-line">
            {data?.items.map((a) => (
              <li
                key={a.audit_id}
                className="flex flex-wrap items-center justify-between gap-3 px-5 py-4"
              >
                <div className="flex min-w-0 flex-col gap-1">
                  <p className="font-medium text-ink">
                    {a.payload?.motivo ?? "Incasso da riconciliare"}
                  </p>
                  <p className="flex flex-wrap gap-x-4 gap-y-1 text-small text-ink-3 tabular-nums">
                    <span>Ordine Revolut: {a.payload?.revolut_order_id ?? "—"}</span>
                    {a.payload?.purchase_id && <span>Purchase: {a.payload.purchase_id}</span>}
                    <span>{formatDateTime(a.created_at)}</span>
                  </p>
                </div>
                {a.risolta ? (
                  <Status tono="chiuso">Risolta</Status>
                ) : (
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
        </Card>
      )}
    </>
  );
}

const SCHEDE = ["acquisti", "fatture", "anomalie"] as const;
type Scheda = (typeof SCHEDE)[number];
const PREFISSO = "pagamenti";

export default function AdminPagamenti() {
  // Scheda nell'URL (`?tab=`): link condivisibili e «indietro» del browser.
  const { tab, setTab } = useTab<Scheda>(SCHEDE);
  // Le anomalie aperte pesano sempre sull'avviso, qualunque sia la scheda
  // attiva (stessa query della sezione: la cache è condivisa).
  const { data: aperte } = useAdminAnomalies("aperta");
  const numAperte = aperte?.items.length ?? 0;

  return (
    <Page variante="elenco">
      <PageHeader
        titolo="Pagamenti"
        descrizione="Storico acquisti, registro fatture e incassi da riconciliare."
        area="admin"
      />

      {numAperte > 0 && (
        <Alert
          tono="attenzione"
          azione={
            tab !== "anomalie" ? (
              <Button variant="ghost" size="sm" onClick={() => setTab("anomalie")}>
                Vedi le anomalie
              </Button>
            ) : undefined
          }
        >
          {numAperte === 1 ? "1 incasso da riconciliare" : `${numAperte} incassi da riconciliare`}
        </Alert>
      )}

      <Tabs
        tabs={[
          { id: "acquisti", label: "Acquisti" },
          { id: "fatture", label: "Fatture" },
          { id: "anomalie", label: "Anomalie", count: numAperte > 0 ? numAperte : undefined },
        ]}
        attivo={tab}
        onChange={setTab}
        ariaLabel="Sezioni dei pagamenti"
        prefisso={PREFISSO}
      />

      <TabPanel id="acquisti" attivo={tab} prefisso={PREFISSO}>
        <SezioneAcquisti />
      </TabPanel>
      <TabPanel id="fatture" attivo={tab} prefisso={PREFISSO}>
        <SezioneFatture />
      </TabPanel>
      <TabPanel id="anomalie" attivo={tab} prefisso={PREFISSO}>
        <SezioneAnomalie />
      </TabPanel>
    </Page>
  );
}
