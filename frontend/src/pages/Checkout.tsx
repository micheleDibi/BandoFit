import RevolutCheckout from "@revolut/checkout";
import { ArrowLeft, Lock, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { BillingProfileForm } from "../components/BillingProfileForm";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ErrorState, Skeleton } from "../components/ui/states";
import { useBillingProfile } from "../hooks/useBillingProfile";
import { useCheckoutPreview, useStartCheckout } from "../hooks/useCheckout";
import { apiErrorCode, apiErrorMessage } from "../lib/api";
import { eurFromCents, formatDate } from "../lib/format";
import { viesApplicabile } from "../lib/paesi";
import { REVOLUT_MODE } from "../lib/revolut";
import { Link } from "react-router-dom";
import type { CheckoutPreview } from "../types";

/** "25.00" → "25": l'aliquota arriva come stringa decimale dal backend. */
const aliquotaDisplay = (aliquota: string) => String(Number(aliquota));

/** Ordine già creato sul provider: basta riaprire il widget con lo stesso
 *  token, l'ordine accetta nuovi tentativi (niente nuovo POST /me/checkout). */
interface PendingOrder {
  purchaseId: string;
  token: string;
}

function RigaRiepilogo({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="text-sm text-slate-600">{label}</dt>
      <dd className="text-sm font-medium tabular-nums text-slate-900">{value}</dd>
    </div>
  );
}

function Riepilogo({ preview }: { preview: CheckoutPreview }) {
  return (
    <>
      <dl className="space-y-2">
        <RigaRiepilogo
          label={
            preview.kind === "piano"
              ? `Piano ${preview.oggetto_nome} (12 mesi)`
              : preview.quantita > 1
                ? `Add-on ${preview.oggetto_nome} — prezzo unitario`
                : `Add-on ${preview.oggetto_nome}`
          }
          value={eurFromCents(preview.listino_cents)}
        />
        {preview.kind === "addon" && preview.quantita > 1 && (
          <RigaRiepilogo label="Quantità" value={`× ${preview.quantita}`} />
        )}
        {preview.credito_cents > 0 && (
          <RigaRiepilogo
            label="Credito per il periodo residuo del piano attuale"
            value={`− ${eurFromCents(preview.credito_cents)}`}
          />
        )}
        <RigaRiepilogo label="Imponibile" value={eurFromCents(preview.imponibile_cents)} />
        <RigaRiepilogo
          label={
            preview.natura_iva
              ? "Reverse charge — IVA assolta nel tuo paese"
              : `IVA ${aliquotaDisplay(preview.iva_aliquota)}%`
          }
          value={eurFromCents(preview.iva_cents)}
        />
        <div className="flex items-baseline justify-between gap-4 border-t border-slate-200 pt-3">
          <dt className="font-display text-base font-semibold text-slate-900">Totale</dt>
          <dd className="font-display text-2xl font-bold tabular-nums text-slate-900">
            {eurFromCents(preview.totale_cents)}
          </dd>
        </div>
      </dl>
      {preview.kind === "piano" && preview.scadenza_risultante && (
        <p className="mt-3 text-sm text-slate-500">
          Nuova scadenza dell'abbonamento:{" "}
          <strong className="text-slate-700">{formatDate(preview.scadenza_risultante)}</strong>
        </p>
      )}
      {preview.natura_iva && (
        <p className="mt-3 text-xs text-slate-400">
          Fattura emessa senza IVA (reverse charge): l'imposta si assolve nel tuo paese.
        </p>
      )}
    </>
  );
}

export default function Checkout() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const piano = searchParams.get("piano");
  const addon = searchParams.get("addon");
  const targetValido = (piano === null) !== (addon === null);

  // `?qty=` (solo addon): clampata nei bound del server (1..100); un valore
  // malformato degrada a 1. Il totale lo calcola SOLO il server (preview).
  const qtyRaw = Number(searchParams.get("qty") ?? "1");
  const quantita =
    addon !== null && Number.isFinite(qtyRaw)
      ? Math.min(100, Math.max(1, Math.trunc(qtyRaw)))
      : 1;

  const target = targetValido
    ? { plan_slug: piano ?? undefined, addon_slug: addon ?? undefined, quantita }
    : {};
  const preview = useCheckoutPreview(target);
  const billing = useBillingProfile();
  const start = useStartCheckout();

  const [autoRenew, setAutoRenew] = useState(true);
  const [pending, setPending] = useState<PendingOrder | null>(null);
  const [opening, setOpening] = useState(false);
  // Esito dell'ultimo tentativo nel widget (annullato o rifiutato): il
  // purchase resta in_attesa e si può ritentare con lo stesso ordine.
  const [payNotice, setPayNotice] = useState<string | null>(null);

  /** Apre il popup Revolut su un ordine già creato. I dati carta vivono SOLO
   *  nel popup del provider: da qui passa esclusivamente il token. */
  const openPopup = async (order: PendingOrder, kind: CheckoutPreview["kind"]) => {
    setPayNotice(null);
    setOpening(true);
    try {
      const rc = await RevolutCheckout(order.token, REVOLUT_MODE);
      rc.payWithPopup({
        // Metodo salvato sul merchant SOLO se l'utente vuole il rinnovo
        // automatico (e solo per i piani: gli addon sono una tantum).
        savePaymentMethodFor: kind === "piano" && autoRenew ? "merchant" : undefined,
        onSuccess: () => navigate(`/app/checkout/esito/${order.purchaseId}`),
        onError: (err) =>
          setPayNotice(
            `Il pagamento non è andato a buon fine${err.message ? ` (${err.message})` : ""}. ` +
              "Nessun addebito: puoi riprovare.",
          ),
        onCancel: () =>
          setPayNotice(
            "Hai chiuso il pagamento senza completarlo. Nessun addebito: puoi riprovare.",
          ),
      });
    } catch {
      setPayNotice("Impossibile aprire il pagamento. Riprova tra qualche istante.");
    } finally {
      setOpening(false);
    }
  };

  const handlePaga = async (kind: CheckoutPreview["kind"]) => {
    // Ordine già creato (tentativo fallito o annullato): si riapre il widget
    // con lo stesso token, senza un nuovo checkout.
    if (pending) {
      await openPopup(pending, kind);
      return;
    }
    setPayNotice(null);
    try {
      const res = await start.mutateAsync({
        ...target,
        auto_renew: kind === "piano" ? autoRenew : false,
      });
      const order = { purchaseId: res.purchase_id, token: res.revolut_order_token };
      setPending(order);
      await openPopup(order, kind);
    } catch {
      // errore mostrato sotto il bottone
    }
  };

  const renderContent = () => {
    if (!targetValido) {
      return (
        <Card className="mt-6 p-6 text-center">
          <p className="text-sm text-slate-600">
            Indica cosa vuoi acquistare partendo dalla pagina Abbonamento.
          </p>
          <LinkButton to="/app/abbonamento" variant="secondary" className="mt-4">
            <ArrowLeft className="size-4" aria-hidden />
            Vai all'abbonamento
          </LinkButton>
        </Card>
      );
    }

    // Account collegato attivo: piano e pagamenti si gestiscono sul titolare.
    if (
      (preview.isError && apiErrorCode(preview.error) === "forbidden") ||
      (billing.isError && apiErrorCode(billing.error) === "forbidden")
    ) {
      return (
        <Card className="mt-6 flex flex-col items-center px-6 py-12 text-center">
          <div className="rounded-full bg-slate-100 p-3 text-slate-500">
            <Lock className="size-7" aria-hidden />
          </div>
          <h2 className="mt-4 font-display text-base font-semibold text-slate-900">
            Gestito dall'account titolare
          </h2>
          <p className="mt-1 max-w-sm text-sm text-slate-500">
            {apiErrorMessage(preview.isError ? preview.error : billing.error)}.
          </p>
        </Card>
      );
    }

    // 400/404 sono risposte di business (piano non superiore, slug sbagliato):
    // il messaggio del server è già la spiegazione, il retry non serve.
    if (
      preview.isError &&
      ["bad_request", "not_found"].includes(apiErrorCode(preview.error) ?? "")
    ) {
      return (
        <Card className="mt-6 p-6 text-center">
          <p className="text-sm text-slate-600">{apiErrorMessage(preview.error)}.</p>
          <LinkButton to="/app/abbonamento" variant="secondary" className="mt-4">
            <ArrowLeft className="size-4" aria-hidden />
            Torna all'abbonamento
          </LinkButton>
        </Card>
      );
    }

    if (preview.isError || billing.isError) {
      return (
        <div className="mt-6">
          <ErrorState
            message={apiErrorMessage(preview.isError ? preview.error : billing.error)}
            onRetry={() => {
              preview.refetch();
              billing.refetch();
            }}
          />
        </div>
      );
    }

    if (preview.isPending || billing.isPending) {
      return (
        <div className="mt-6 space-y-4">
          <Skeleton className="h-56 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      );
    }

    const dati = preview.data;
    const billingMancante = billing.data === null;
    // Rete per il fail-open del VIES: un'azienda UE senza prova valida paga
    // il 25% e potrebbe non essersene accorta (il form si chiude al salvataggio).
    const b = billing.data;
    const invitoVies =
      !!b &&
      b.tipo_soggetto === "azienda" &&
      viesApplicabile(b.paese) &&
      b.vies_valid !== true;

    return (
      <>
        <Card className="mt-6 p-6">
          <Riepilogo preview={dati} />
        </Card>

        {invitoVies && (
          <p className="mt-3 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">
            Sei un'azienda UE? Con la partita IVA verificata nel VIES l'acquisto è in
            reverse charge, senza IVA.{" "}
            <Link to="/app/fatturazione" className="font-medium underline">
              Verifica dai dati di fatturazione
            </Link>
            .
          </p>
        )}

        {/* Senza anagrafica di fatturazione niente pagamento: il backend la
            congela nella fattura, quindi si completa QUI, prima di pagare.
            Al salvataggio la preview si ricalcola (l'IVA dipende dal
            soggetto: un'azienda UE passa in reverse charge). */}
        {billingMancante ? (
          <Card className="mt-4 p-6">
            <h2 className="font-display text-base font-semibold text-slate-900">
              Completa i dati di fatturazione
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              Servono per intestare la fattura dell'acquisto: un minuto e torni al pagamento.
            </p>
            <div className="mt-4">
              <BillingProfileForm profile={null} onSaved={() => preview.refetch()} />
            </div>
          </Card>
        ) : (
          <Card className="mt-4 p-6">
            {dati.kind === "piano" && (
              <label className="mb-4 flex cursor-pointer items-start gap-2.5">
                <input
                  type="checkbox"
                  className="mt-0.5 size-4 cursor-pointer accent-brand-500"
                  checked={autoRenew}
                  // La scelta è congelata nell'ordine creato: si sblocca solo
                  // con un nuovo checkout, non tra un tentativo e l'altro.
                  disabled={!!pending || start.isPending || opening}
                  onChange={(e) => setAutoRenew(e.target.checked)}
                />
                <span>
                  <span className="block text-sm font-medium text-slate-700">
                    Rinnova automaticamente alla scadenza
                  </span>
                  <span className="mt-0.5 block text-xs text-slate-500">
                    Ti avvisiamo via email almeno 7 giorni prima dell'addebito; puoi disdire
                    quando vuoi.
                  </span>
                </span>
              </label>
            )}

            <Button
              className="w-full"
              size="lg"
              onClick={() => handlePaga(dati.kind)}
              loading={start.isPending || opening}
            >
              {pending ? "Riprova il pagamento" : `Paga ${eurFromCents(dati.totale_cents)}`}
            </Button>
            <p className="mt-2 inline-flex items-center gap-1.5 text-xs text-slate-400">
              <ShieldCheck className="size-3.5 shrink-0" aria-hidden />
              Il pagamento avviene nel popup di Revolut: i dati della carta non passano mai da
              BandoFit.
            </p>

            {payNotice && (
              <p
                className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800"
                role="alert"
              >
                {payNotice}
              </p>
            )}
            {start.isError && (
              <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
                {apiErrorMessage(start.error)}.
                {apiErrorCode(start.error) === "conflict" && (
                  <p className="mt-1">
                    <LinkButton to="/app/acquisti" variant="secondary" size="sm">
                      Vedi i tuoi acquisti
                    </LinkButton>
                  </p>
                )}
              </div>
            )}
          </Card>
        )}
      </>
    );
  };

  return (
    <div className="mx-auto max-w-2xl">
      <h1 className="font-display text-2xl font-bold tracking-tight text-slate-900">Checkout</h1>
      <p className="mt-1 text-sm text-slate-500">
        Controlla il riepilogo e completa il pagamento nel popup sicuro di Revolut.
      </p>
      {renderContent()}
    </div>
  );
}
