import { Receipt } from "lucide-react";
import { useEffect, useState } from "react";
import { usePurchases } from "../../hooks/useCheckout";
import { apiErrorMessage } from "../../lib/api";
import { cn } from "../../lib/cn";
import { PURCHASE_STATO_LABELS } from "../../lib/copy";
import { eurFromCents, formatDateTime } from "../../lib/format";
import type { PurchaseStatus } from "../../types";
import { Badge } from "../ui/Badge";
import { LinkButton } from "../ui/Button";
import { Card } from "../ui/Card";
import { Pagination } from "../ui/Pagination";
import { Status, type TonoStatus } from "../ui/Status";
import { EmptyState, ErrorState, Skeleton } from "../ui/states";
import { Table, Td, Th } from "../ui/Table";
import { TextLink } from "../ui/TextLink";

/** Lo stato dell'acquisto in parole: la parola da `PURCHASE_STATO_LABELS`, il tono qui. */
const TONI_STATO: Record<PurchaseStatus, TonoStatus> = {
  in_attesa: "in-apertura",
  pagato: "aperto",
  fallito: "errore",
  scaduto: "chiuso",
  annullato: "chiuso",
  gratuito: "neutro",
};

/** Bordo sinistro della riga nel colore dello stato (lo stesso di `Status`, che
 *  porta la parola): in attesa accent, pagato fit, fallito danger, il resto neutro. */
const BORDO_STATO: Record<PurchaseStatus, string> = {
  in_attesa: "border-l-4 border-l-accent",
  pagato: "border-l-4 border-l-fit",
  fallito: "border-l-4 border-l-danger",
  scaduto: "border-l-4 border-l-line-control",
  annullato: "border-l-4 border-l-line-control",
  gratuito: "border-l-4 border-l-line-control",
};

/** Scheda «Acquisti» dell'Abbonamento: lo storico di piani e add-on in tabella. */
export function SchedaAcquisti() {
  const [page, setPage] = useState(1);
  const { data, isPending, isError, error, refetch, isPlaceholderData } = usePurchases(page);

  // Su una pagina > 1 rimasta vuota (totale calato) si rientra sull'ultima.
  useEffect(() => {
    if (data && page > 1 && data.items.length === 0 && data.total > 0) {
      setPage(Math.max(1, data.total_pages));
    }
  }, [data, page]);

  return (
    <section aria-label="Acquisti" aria-busy={isPending || isPlaceholderData}>
      {isPending ? (
        <div className="flex flex-col gap-3" aria-hidden>
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : isError ? (
        <ErrorState
          title="Non siamo riusciti a caricare gli acquisti."
          message={apiErrorMessage(error)}
          onRetry={() => refetch()}
        />
      ) : data && data.items.length === 0 ? (
        <EmptyState
          icon={Receipt}
          area="account"
          title="Nessun acquisto ancora."
          description="Quando acquisti un piano o un add-on lo trovi qui, con il suo stato."
          action={
            <LinkButton to="?tab=piano" variant="secondary">
              Vedi i piani
            </LinkButton>
          }
        />
      ) : (
        <div className="flex flex-col gap-6">
          <Card className="p-0 sm:p-2">
            <Table className={cn(isPlaceholderData && "opacity-60 transition-opacity")}>
              <thead>
                <tr>
                  <Th>Data</Th>
                  <Th>Descrizione</Th>
                  <Th>Stato</Th>
                  <Th numerica>Importo</Th>
                  <Th>
                    <span className="sr-only">Azione</span>
                  </Th>
                </tr>
              </thead>
              <tbody>
                {data?.items.map((p) => {
                  const amministrativo = p.kind === "cambio_admin" || p.kind === "addon_admin";
                  return (
                    <tr key={p.id}>
                      <Td
                        className={cn(
                          "whitespace-nowrap text-small text-ink-2 tabular-nums",
                          BORDO_STATO[p.status],
                        )}
                      >
                        {formatDateTime(p.created_at)}
                      </Td>
                      <Td>
                        <span className="block font-medium text-ink">{p.descrizione}</span>
                        {amministrativo && (
                          <span className="mt-1 block">
                            <Badge area="admin">Operazione dell'amministrazione</Badge>
                          </span>
                        )}
                        {amministrativo && p.motivazione && (
                          <span className="mt-1 block text-small text-ink-2">{p.motivazione}</span>
                        )}
                      </Td>
                      <Td>
                        <Status tono={TONI_STATO[p.status]}>{PURCHASE_STATO_LABELS[p.status]}</Status>
                      </Td>
                      <Td numerica className="font-semibold text-ink">
                        {eurFromCents(p.totale_cents)}
                      </Td>
                      <Td className="text-right">
                        {p.status === "in_attesa" && (
                          <TextLink to={`/app/checkout/esito/${p.id}`} className="text-small font-medium">
                            Verifica lo stato
                          </TextLink>
                        )}
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          </Card>
          <Pagination
            page={page}
            totalPages={data?.total_pages ?? 1}
            onChange={(next) => {
              setPage(next);
              window.scrollTo({ top: 0, behavior: "smooth" });
            }}
          />
        </div>
      )}
    </section>
  );
}
