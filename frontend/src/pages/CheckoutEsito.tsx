import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
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

  // Pagamento confermato: piano/quote/scadenza possono essere cambiati.
  const pagato = purchase?.status === "pagato" || purchase?.status === "gratuito";
  useEffect(() => {
    if (pagato) queryClient.invalidateQueries({ queryKey: ["me"] });
  }, [pagato, queryClient]);

  const renderStato = (p: Purchase) => {
    if (p.status === "pagato" || p.status === "gratuito") {
      return (
        <Card className="flex flex-col items-center px-6 py-12 text-center">
          <div className="rounded-full bg-emerald-50 p-3 text-emerald-600">
            <CheckCircle2 className="size-8" aria-hidden />
          </div>
          <h2 className="mt-4 font-display text-xl font-bold text-slate-900">
            Pagamento riuscito, grazie!
          </h2>
          <p className="mt-2 max-w-md text-sm text-slate-600">
            {p.descrizione}
            {p.totale_cents > 0 && <> — totale {eurFromCents(p.totale_cents)}</>}.{" "}
            {p.kind === "addon"
              ? "L'add-on è attivo sul tuo account."
              : p.kind === "cambio_admin"
                ? "L'operazione è stata registrata."
                : "Il nuovo piano è attivo da subito."}
          </p>
          <div className="mt-6 flex flex-wrap justify-center gap-3">
            {p.kind === "addon" ? (
              <LinkButton to="/app/abbonamento">Vedi i tuoi add-on</LinkButton>
            ) : (
              <LinkButton to="/app/abbonamento">Vai al tuo abbonamento</LinkButton>
            )}
            <LinkButton to="/app/acquisti" variant="secondary">
              I tuoi acquisti
            </LinkButton>
          </div>
        </Card>
      );
    }

    if (p.status === "in_attesa") {
      return (
        <Card className="flex flex-col items-center px-6 py-12 text-center">
          <div className="rounded-full bg-brand-50 p-3 text-brand-500">
            <Loader2 className="size-8 animate-spin" aria-hidden />
          </div>
          <h2 className="mt-4 font-display text-xl font-bold text-slate-900">
            Stiamo confermando il pagamento
          </h2>
          <p className="mt-2 max-w-md text-sm text-slate-600" role="status">
            {pollScaduto
              ? "La conferma del provider sta impiegando più del previsto. Puoi verificare ora oppure ricontrollare più tardi da «I tuoi acquisti»: se il pagamento è andato a buon fine non verrà perso."
              : "Attendiamo la conferma del provider di pagamento: di solito bastano pochi secondi, la pagina si aggiorna da sola."}
          </p>
          <div className="mt-6 flex flex-wrap justify-center gap-3">
            <Button onClick={() => purchaseId && sync.mutate(purchaseId)} loading={sync.isPending}>
              <RefreshCw className="size-4" aria-hidden />
              Verifica ora
            </Button>
            {pollScaduto && (
              <LinkButton to="/app/acquisti" variant="secondary">
                I tuoi acquisti
              </LinkButton>
            )}
          </div>
          {sync.isError && (
            <p className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
              {apiErrorMessage(sync.error)}
            </p>
          )}
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
      <Card className="flex flex-col items-center px-6 py-12 text-center">
        <div className="rounded-full bg-amber-50 p-3 text-amber-600">
          <AlertTriangle className="size-8" aria-hidden />
        </div>
        <h2 className="mt-4 font-display text-xl font-bold text-slate-900">
          Pagamento non completato
        </h2>
        <p className="mt-2 max-w-md text-sm text-slate-600">{messaggi[p.status]}</p>
        {p.decline_reason && (
          <p className="mt-1 text-xs text-slate-400">
            Motivo segnalato dal provider: {p.decline_reason}
          </p>
        )}
        <div className="mt-6 flex flex-wrap justify-center gap-3">
          {retry && <LinkButton to={retry}>Riprova l'acquisto</LinkButton>}
          <LinkButton to="/app/acquisti" variant="secondary">
            I tuoi acquisti
          </LinkButton>
        </div>
      </Card>
    );
  };

  return (
    <div className="mx-auto max-w-2xl">
      <h1 className="font-display text-2xl font-bold tracking-tight text-slate-900">
        Esito del pagamento
      </h1>
      {isPending ? (
        <div className="mt-6">
          <Skeleton className="h-64 w-full" />
        </div>
      ) : isError || !purchase ? (
        <div className="mt-6">
          <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
        </div>
      ) : (
        <div className="mt-6">{renderStato(purchase)}</div>
      )}
    </div>
  );
}
