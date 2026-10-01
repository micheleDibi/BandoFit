import RevolutCheckout from "@revolut/checkout";
import { CreditCard, ReceiptText, ShieldCheck, ShoppingCart } from "lucide-react";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { BillingProfileForm } from "../components/BillingProfileForm";
import { Alert } from "../components/ui/Alert";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Checkbox } from "../components/ui/Checkbox";
import { IconChip } from "../components/ui/IconChip";
import { InlineError } from "../components/ui/InlineError";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { Section, SectionHeader } from "../components/ui/SectionHeader";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { TextLink } from "../components/ui/TextLink";
import { useBillingProfile } from "../hooks/useBillingProfile";
import { useCheckoutPreview, useStartCheckout } from "../hooks/useCheckout";
import { apiErrorCode, apiErrorMessage } from "../lib/api";
import { eurFromCents, formatDate } from "../lib/format";
import { viesApplicabile } from "../lib/paesi";
import { REVOLUT_MODE } from "../lib/revolut";
import type { CheckoutPreview } from "../types";

const INDIETRO = { label: "Abbonamento", to: "/app/abbonamento" };

/** Titolo di sezione con l'`IconChip` dell'area account accanto. */
function TitoloConIcona({ icon, children }: { icon: typeof CreditCard; children: string }) {
  return (
    <span className="flex items-center gap-3">
      <IconChip icon={icon} area="account" size="sm" />
      {children}
    </span>
  );
}

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
      <dt className="text-body text-ink-2">{label}</dt>
      <dd className="text-body font-medium text-ink tabular-nums">{value}</dd>
    </div>
  );
}

function Riepilogo({ preview }: { preview: CheckoutPreview }) {
  return (
    <div className="flex flex-col gap-3">
      <dl className="flex flex-col gap-2">
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
        <div className="mt-1 flex items-baseline justify-between gap-4 rounded-control bg-accent-soft px-4 py-3">
          <dt className="text-title-group text-ink">Totale</dt>
          <dd className="text-figure text-ink">{eurFromCents(preview.totale_cents)}</dd>
        </div>
      </dl>
      {preview.kind === "piano" && preview.scadenza_risultante && (
        <p className="text-body text-ink-2">
          Nuova scadenza dell'abbonamento:{" "}
          <strong className="text-ink">{formatDate(preview.scadenza_risultante)}</strong>
        </p>
      )}
      {preview.natura_iva && (
        <p className="text-small text-ink-3">
          Fattura emessa senza IVA (reverse charge): l'imposta si assolve nel tuo paese.
        </p>
      )}
    </div>
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

  const tornaAllAbbonamento = (
    <LinkButton to="/app/abbonamento" variant="secondary">
      Torna all'abbonamento
    </LinkButton>
  );

  const renderContent = () => {
    if (!targetValido) {
      return (
        <EmptyState
          icon={ShoppingCart}
          area="account"
          title="Indica cosa vuoi acquistare partendo dalla pagina Abbonamento."
          action={tornaAllAbbonamento}
        />
      );
    }

    // Account collegato attivo: piano e pagamenti si gestiscono sul titolare.
    if (
      (preview.isError && apiErrorCode(preview.error) === "forbidden") ||
      (billing.isError && apiErrorCode(billing.error) === "forbidden")
    ) {
      return (
        <EmptyState
          area="account"
          title="Gestito dall'account titolare."
          description={`${apiErrorMessage(preview.isError ? preview.error : billing.error)}.`}
          action={tornaAllAbbonamento}
        />
      );
    }

    // 400/404 sono risposte di business (piano non superiore, slug sbagliato):
    // il messaggio del server è già la spiegazione, il retry non serve.
    if (
      preview.isError &&
      ["bad_request", "not_found"].includes(apiErrorCode(preview.error) ?? "")
    ) {
      return (
        <EmptyState
          icon={ShoppingCart}
          area="account"
          title={`${apiErrorMessage(preview.error)}.`}
          action={tornaAllAbbonamento}
        />
      );
    }

    if (preview.isError || billing.isError) {
      return (
        <ErrorState
          title="Non siamo riusciti a preparare il pagamento."
          message={apiErrorMessage(preview.isError ? preview.error : billing.error)}
          onRetry={() => {
            preview.refetch();
            billing.refetch();
          }}
        />
      );
    }

    if (preview.isPending || billing.isPending) {
      return (
        <div className="flex flex-col gap-4" aria-hidden>
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-12 w-full" />
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
        <Card>
          <Section aria-label="Riepilogo">
            <SectionHeader titolo={<TitoloConIcona icon={ShoppingCart}>Riepilogo</TitoloConIcona>} />
            <Riepilogo preview={dati} />
          </Section>
        </Card>

        {invitoVies && (
          <Alert tono="attenzione">
            Sei un'azienda UE? Con la partita IVA verificata nel VIES l'acquisto è in reverse
            charge, senza IVA.{" "}
            <TextLink to="/app/abbonamento?tab=pagamento">Verifica dai dati di fatturazione</TextLink>
            .
          </Alert>
        )}

        {/* Senza anagrafica di fatturazione niente pagamento: il backend la
            congela nella fattura, quindi si completa QUI, prima di pagare.
            Al salvataggio la preview si ricalcola (l'IVA dipende dal
            soggetto: un'azienda UE passa in reverse charge). */}
        {billingMancante ? (
          <Card>
            <Section aria-label="Dati di fatturazione">
              <SectionHeader
                titolo={<TitoloConIcona icon={ReceiptText}>Completa i dati di fatturazione</TitoloConIcona>}
              />
              <p className="text-body text-ink-2">
                Servono per intestare la fattura dell'acquisto: un minuto e torni al pagamento.
              </p>
              <BillingProfileForm profile={null} onSaved={() => preview.refetch()} />
            </Section>
          </Card>
        ) : (
          <Card area="account">
            <Section aria-label="Pagamento">
              <SectionHeader titolo={<TitoloConIcona icon={CreditCard}>Pagamento</TitoloConIcona>} />
              {dati.kind === "piano" && (
                <Checkbox
                  label="Rinnova automaticamente alla scadenza"
                  descrizione="Ti avvisiamo via email almeno 7 giorni prima dell'addebito; puoi disdire quando vuoi."
                  checked={autoRenew}
                  // La scelta è congelata nell'ordine creato: si sblocca solo
                  // con un nuovo checkout, non tra un tentativo e l'altro.
                  disabled={!!pending || start.isPending || opening}
                  onChange={(e) => setAutoRenew(e.target.checked)}
                />
              )}
              <div>
                <Button
                  type="button"
                  size="lg"
                  onClick={() => handlePaga(dati.kind)}
                  loading={start.isPending || opening}
                >
                  {pending ? "Riprova il pagamento" : `Paga ${eurFromCents(dati.totale_cents)}`}
                </Button>
              </div>
              <p className="inline-flex items-start gap-1.5 text-small text-ink-3">
                <ShieldCheck className="mt-0.5 size-4 shrink-0 text-fit-ink" aria-hidden />
                Il pagamento avviene nel popup di Revolut: i dati della carta non passano mai da
                BandoFit.
              </p>
              {payNotice && <Alert tono="attenzione">{payNotice}</Alert>}
              {start.isError && (
                <div className="flex flex-col gap-2">
                  <InlineError>{apiErrorMessage(start.error)}.</InlineError>
                  {apiErrorCode(start.error) === "conflict" && (
                    <div>
                      <LinkButton to="/app/abbonamento?tab=acquisti" variant="secondary" size="sm">
                        Vedi gli acquisti
                      </LinkButton>
                    </div>
                  )}
                </div>
              )}
            </Section>
          </Card>
        )}
      </>
    );
  };

  return (
    <Page variante="flusso">
      <PageHeader
        area="account"
        indietro={INDIETRO}
        titolo="Checkout"
        descrizione="Controlla il riepilogo e completa il pagamento nel popup sicuro di Revolut."
      />
      {renderContent()}
    </Page>
  );
}
