import { useQueryClient } from "@tanstack/react-query";
import { Check, RefreshCw, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { InlineError } from "../components/ui/InlineError";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { Spinner } from "../components/ui/Spinner";
import { ErrorState, Skeleton } from "../components/ui/states";
import { usePurchase, useSyncPurchase } from "../hooks/useCheckout";
import { apiErrorMessage } from "../lib/api";
import { eurFromCents } from "../lib/format";
import type { Purchase } from "../types";

// Il webhook di norma arriva in pochi secondi: 2s di polling per ~90s coprono
// anche un provider lento; oltre, resta il «Verifica ora» manuale.
const POLL_INTERVAL_MS = 2_000;
const POLL_MAX_MS = 90_000;

/** Dove riprovare l'acquisto fallito: stesso oggetto, nuovo checkout. */
function retryUrl(purchase: Purchase): string | null {
  if (purchase.kind === "addon")
    return `/app/checkout?addon=${purchase.oggetto_slug}${
      purchase.quantita > 1 ? `&qty=${purchase.quantita}` : ""
    }`;
  if (purchase.kind === "piano" || purchase.kind === "rinnovo")
    return `/app/checkout?piano=${purchase.oggetto_slug}`;
  return null; // cambio_admin: non è un flusso self-serve
}

/** Card dell'esito: bordo sinistro nel colore dello stato (riuscito fit, in
 *  attesa accent, non completato warning); la parola sta nel titolo. */
const BORDO_ESITO = {
  riuscito: "border-l-4 border-l-fit",
  attesa: "border-l-4 border-l-accent",
  fallito: "border-l-4 border-l-warning",
} as const;

/** L'icona dell'esito in un quadrato nel tono dello stato (decorativa: il
 *  titolo accanto dice com'è andata). */
const CHIP_ESITO = {
  riuscito: "bg-fit-soft text-fit-ink",
  attesa: "bg-accent-soft text-accent-hover",
  fallito: "bg-warning-soft text-warning-ink",
} as const;

function ChipEsito({ tono, children }: { tono: keyof typeof CHIP_ESITO; children: React.ReactNode }) {
  return (
    <span
      aria-hidden
      className={`inline-flex size-10 shrink-0 items-center justify-center rounded-control ${CHIP_ESITO[tono]}`}
    >
      {children}
    </span>
  );
}

const ACQUISTI = (
  <LinkButton to="/app/abbonamento?tab=acquisti" variant="secondary">
    Vedi gli acquisti
  </LinkButton>
);

export default function CheckoutEsito() {
  const { purchaseId } = useParams<{ purchaseId: string }>();
  const queryClient = useQueryClient();
  const [pollScaduto, setPollScaduto] = useState(false);

  const {
    data: purchase,
    isPending,
    isError,
    error,
    refetch,
  } = usePurchase(purchaseId, !pollScaduto, POLL_INTERVAL_MS);
  const sync = useSyncPurchase();

  useEffect(() => {
    const timer = setTimeout(() => setPollScaduto(true), POLL_MAX_MS);
    return () => clearTimeout(timer);
  }, []);

  // Pagamento confermato: piano/quote/scadenza possono essere cambiati —
  // e con un addon allocativo anche i limiti effettivi e l'acquistabilità.
  const pagato = purchase?.status === "pagato" || purchase?.status === "gratuito";
  useEffect(() => {
    if (pagato) {
      queryClient.invalidateQueries({ queryKey: ["me"] });
      queryClient.invalidateQueries({ queryKey: ["entitlements"] });
      queryClient.invalidateQueries({ queryKey: ["addons"] });
    }
  }, [pagato, queryClient]);

  const renderStato = (p: Purchase) => {
    if (p.status === "pagato" || p.status === "gratuito") {
      return (
        <Card className={`flex flex-col items-start gap-3 sm:p-8 ${BORDO_ESITO.riuscito}`}>
          <h2 className="flex items-center gap-3 text-title-section text-ink">
            <ChipEsito tono="riuscito">
              <Check className="size-5" />
            </ChipEsito>
            Pagamento riuscito, grazie!
          </h2>
          <p className="text-body text-ink-2">
            {p.descrizione}
            {p.totale_cents > 0 && <> — totale {eurFromCents(p.totale_cents)}</>}.{" "}
            {p.kind === "addon"
              ? "L'add-on è attivo sul tuo account."
              : p.kind === "cambio_admin"
                ? "L'operazione è stata registrata."
                : "Il nuovo piano è attivo da subito."}
          </p>
          <div className="flex flex-wrap gap-2 pt-1">
            {p.kind === "addon" ? (
              <LinkButton to="/app/abbonamento?tab=addon">Vedi i tuoi add-on</LinkButton>
            ) : (
              <LinkButton to="/app/abbonamento">Vai al tuo abbonamento</LinkButton>
            )}
            {ACQUISTI}
          </div>
        </Card>
      );
    }

    if (p.status === "in_attesa") {
      return (
        <Card className={`flex flex-col items-start gap-3 sm:p-8 ${BORDO_ESITO.attesa}`}>
          <h2 className="flex items-center gap-3 text-title-section text-ink">
            <ChipEsito tono="attesa">
              <Spinner size="md" className="text-current" />
            </ChipEsito>
            Stiamo confermando il pagamento
          </h2>
          <p className="text-body text-ink-2" role="status">
            {pollScaduto
              ? "La conferma del provider sta impiegando più del previsto. Puoi verificare ora oppure ricontrollare più tardi dagli acquisti: se il pagamento è andato a buon fine non verrà perso."
              : "Attendiamo la conferma del provider di pagamento: di solito bastano pochi secondi, la pagina si aggiorna da sola."}
          </p>
          <div className="flex flex-wrap gap-2 pt-1">
            <Button
              type="button"
              onClick={() => purchaseId && sync.mutate(purchaseId)}
              loading={sync.isPending}
            >
              <RefreshCw className="size-4" aria-hidden />
              Verifica ora
            </Button>
            {pollScaduto && ACQUISTI}
          </div>
          {sync.isError && <InlineError>{apiErrorMessage(sync.error)}</InlineError>}
        </Card>
      );
    }

    // fallito / scaduto / annullato
    const messaggi: Record<string, string> = {
      fallito: "Il pagamento non è andato a buon fine e non è stato addebitato nulla.",
      scaduto: "L'ordine di pagamento è scaduto senza essere completato: nessun addebito.",
      annullato: "Il pagamento è stato annullato: nessun addebito.",
    };
    const retry = retryUrl(p);
    return (
      <Card className={`flex flex-col items-start gap-3 sm:p-8 ${BORDO_ESITO.fallito}`}>
        <h2 className="flex items-center gap-3 text-title-section text-ink">
          <ChipEsito tono="fallito">
            <TriangleAlert className="size-5" />
          </ChipEsito>
          Pagamento non completato
        </h2>
        <p className="text-body text-ink-2">{messaggi[p.status]}</p>
        {p.decline_reason && (
          <p className="text-small text-ink-3">Motivo segnalato dal provider: {p.decline_reason}</p>
        )}
        <div className="flex flex-wrap gap-2 pt-1">
          {retry && <LinkButton to={retry}>Riprova l'acquisto</LinkButton>}
          {ACQUISTI}
        </div>
      </Card>
    );
  };

  return (
    <Page variante="flusso">
      <PageHeader area="account" titolo="Esito del pagamento" />
      {isPending ? (
        <Skeleton className="h-40 w-full" />
      ) : isError || !purchase ? (
        <ErrorState
          title="Non siamo riusciti a leggere l'esito del pagamento."
          message={apiErrorMessage(error)}
          onRetry={() => refetch()}
        />
      ) : (
        renderStato(purchase)
      )}
    </Page>
  );
}
