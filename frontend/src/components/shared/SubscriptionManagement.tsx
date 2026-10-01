import RevolutCheckout from "@revolut/checkout";
import { CreditCard } from "lucide-react";
import { useEffect, useState } from "react";
import {
  useCancelScheduledChange,
  useRemoveMethod,
  useScheduleDowngrade,
  useSetAutoRenew,
  useStartAddMethod,
  useSubscriptionManagement,
} from "../../hooks/useSubscriptionManagement";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { formatDateNumeric } from "../../lib/format";
import { REVOLUT_MODE } from "../../lib/revolut";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { InlineError } from "../ui/InlineError";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { Spinner } from "../ui/Spinner";
import { Skeleton } from "../ui/states";
import { Switch } from "../ui/Switch";

// Dopo l'onSuccess del widget il metodo compare via riconciliazione, ma in
// dev il webhook non è garantito: breve polling, poi si passa al messaggio
// «aggiorna tra poco».
const METHOD_POLL_MAX_MS = 20_000;

/** Sezione «Pagamento e rinnovo» dell'Abbonamento: metodo salvato, rinnovo
 *  automatico, disdetta e cambio programmato. Con un piano gratuito e nessun
 *  cambio programmato dice solo che non c'è nulla da gestire. Il flusso del
 *  provider (popup e token) è invariato: i dati carta vivono SOLO nel popup. */
export function SubscriptionManagement({
  pianoAPagamento,
  pianoNome,
}: {
  /** Il piano attivo è a pagamento (dal profilo utente). */
  pianoAPagamento: boolean;
  pianoNome: string | null;
}) {
  const [pollMetodo, setPollMetodo] = useState(false);
  const management = useSubscriptionManagement(pollMetodo);
  const autoRenew = useSetAutoRenew();
  const downgrade = useScheduleDowngrade();
  const annullaCambio = useCancelScheduledChange();
  const startAddMethod = useStartAddMethod();
  const removeMethod = useRemoveMethod();

  const [opening, setOpening] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [pollScaduto, setPollScaduto] = useState(false);
  // Rinnovo chiesto senza metodo (409): a carta registrata si riaccende da solo.
  const [rinnovoDopoMetodo, setRinnovoDopoMetodo] = useState(false);
  const [confermaDisdetta, setConfermaDisdetta] = useState(false);
  const [confermaRimozione, setConfermaRimozione] = useState(false);

  const data = management.data;
  const metodoPresente = !!data?.metodo.presente;

  // Metodo comparso: fine del salvataggio; se il toggle rinnovo era l'intento
  // originale (409 per metodo mancante), lo si completa ora.
  useEffect(() => {
    if (!metodoPresente || (!pollMetodo && !pollScaduto)) return;
    setPollMetodo(false);
    setPollScaduto(false);
    setNotice(null);
    if (rinnovoDopoMetodo) {
      setRinnovoDopoMetodo(false);
      autoRenew.mutate(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [metodoPresente, pollMetodo, pollScaduto, rinnovoDopoMetodo]);

  useEffect(() => {
    if (!pollMetodo) return;
    const timer = setTimeout(() => {
      setPollMetodo(false);
      setPollScaduto(true);
    }, METHOD_POLL_MAX_MS);
    return () => clearTimeout(timer);
  }, [pollMetodo]);

  /** Widget a 0 €: salva la carta senza acquisto. I dati carta vivono SOLO
   *  nel popup del provider. */
  const handleAddMethod = async (poiAttivaRinnovo = false) => {
    setNotice(null);
    setPollScaduto(false);
    setOpening(true);
    try {
      const { revolut_order_token } = await startAddMethod.mutateAsync();
      const rc = await RevolutCheckout(revolut_order_token, REVOLUT_MODE);
      rc.payWithPopup({
        savePaymentMethodFor: "merchant",
        onSuccess: () => {
          setRinnovoDopoMetodo(poiAttivaRinnovo);
          setPollMetodo(true);
        },
        onError: (err) =>
          setNotice(
            `Non siamo riusciti a salvare la carta${err.message ? ` (${err.message})` : ""}. Riprova.`,
          ),
        onCancel: () => setRinnovoDopoMetodo(false),
      });
    } catch (err) {
      setNotice(apiErrorMessage(err));
    } finally {
      setOpening(false);
    }
  };

  const handleToggleRenew = async (enabled: boolean) => {
    setNotice(null);
    try {
      await autoRenew.mutateAsync(enabled);
    } catch (err) {
      setNotice(apiErrorMessage(err));
      // Manca il metodo salvato: si apre subito il flusso di aggiunta e a
      // carta registrata il rinnovo si attiva da solo.
      if (enabled && apiErrorCode(err) === "conflict") {
        await handleAddMethod(true);
      }
    }
  };

  const handleDisdetta = async () => {
    try {
      await downgrade.mutateAsync("gratuito");
      setConfermaDisdetta(false);
    } catch {
      // errore mostrato nella finestra
    }
  };

  const handleRimuovi = async () => {
    setNotice(null);
    try {
      await removeMethod.mutateAsync();
      setConfermaRimozione(false);
    } catch {
      // errore mostrato nella finestra
    }
  };

  // Niente piano a pagamento e niente cambio da mostrare: nulla da gestire.
  // (Dopo tutti gli hook.)
  const nullaDaGestire = !pianoAPagamento && !data?.cambio_programmato;
  const testoNulla = (
    <p className="text-body text-ink-2">
      Con il piano attuale non c'è un rinnovo da gestire: il metodo di pagamento si chiede al
      primo acquisto.
    </p>
  );

  return (
    <Section aria-label="Pagamento e rinnovo">
      <SectionHeader titolo="Pagamento e rinnovo" />
      {/* Prima il piano: a chi non paga un errore della lettura non riguarda
          nulla (come in HEAD, dove la sezione non compariva). */}
      {!pianoAPagamento && management.isError ? (
        testoNulla
      ) : management.isError ? (
        <div className="flex flex-col gap-2">
          <InlineError>{apiErrorMessage(management.error)}</InlineError>
          <div>
            <Button type="button" variant="secondary" size="sm" onClick={() => management.refetch()}>
              Riprova
            </Button>
          </div>
        </div>
      ) : management.isPending || !data ? (
        <div className="flex flex-col gap-3" aria-hidden>
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-2/3" />
        </div>
      ) : nullaDaGestire ? (
        testoNulla
      ) : (
        <div className="flex flex-col">
          {/* Cambio programmato: informa e lascia annullare */}
          {data.cambio_programmato && (
            <div className="border-b border-line pb-4">
              <Alert
                tono="attenzione"
                titolo={
                  data.cambio_programmato.motivo === "disdetta"
                    ? "Disdetta programmata"
                    : "Downgrade programmato"
                }
                azione={
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => annullaCambio.mutate()}
                    loading={annullaCambio.isPending}
                  >
                    Annulla
                  </Button>
                }
              >
                Passerai a {data.cambio_programmato.to_plan_nome} il{" "}
                {formatDateNumeric(data.cambio_programmato.effective_date)}. Fino ad allora resta
                tutto attivo.
                {annullaCambio.isError && (
                  <InlineError className="mt-2">{apiErrorMessage(annullaCambio.error)}</InlineError>
                )}
              </Alert>
            </div>
          )}

          {/* Metodo di pagamento salvato */}
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line py-4">
            <div className="flex flex-col gap-0.5">
              <p className="text-body font-semibold text-ink">Metodo di pagamento</p>
              <p className="inline-flex items-center gap-1.5 text-body text-ink-2">
                <CreditCard className="size-4 shrink-0 text-ink-3" aria-hidden />
                {metodoPresente ? (data.metodo.label ?? "Metodo salvato") : "Nessun metodo salvato"}
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => handleAddMethod(false)}
                loading={opening || startAddMethod.isPending}
                disabled={pollMetodo}
              >
                {metodoPresente ? "Sostituisci" : "Aggiungi un metodo"}
              </Button>
              {metodoPresente && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setConfermaRimozione(true)}
                >
                  Rimuovi
                </Button>
              )}
            </div>
          </div>

          {/* Rinnovo automatico */}
          {pianoAPagamento && (
            <div className="flex flex-col gap-4 border-b border-line py-4">
              <Switch
                label="Rinnovo automatico"
                descrizione="Ti avvisiamo via email almeno 7 giorni prima di ogni addebito. Puoi disdire quando vuoi."
                checked={data.auto_renew}
                disabled={autoRenew.isPending}
                onChange={handleToggleRenew}
              />
              {/* Disdetta: solo con rinnovo attivo e nessun cambio già programmato */}
              {!data.cambio_programmato &&
                (data.auto_renew ? (
                  <div>
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={() => setConfermaDisdetta(true)}
                    >
                      Disdici il rinnovo
                    </Button>
                  </div>
                ) : (
                  data.data_scadenza && (
                    <p className="text-small text-ink-3">
                      Il piano non si rinnova da solo: resta attivo fino al{" "}
                      {formatDateNumeric(data.data_scadenza)}.
                    </p>
                  )
                ))}
            </div>
          )}

          {/* Area di stato condivisa (salvataggio carta, errori) */}
          {(pollMetodo || (pollScaduto && !metodoPresente) || notice) && (
            <div className="flex flex-col gap-2 py-4">
              {pollMetodo && (
                <p className="inline-flex items-center gap-2 text-body text-ink-2" role="status">
                  <Spinner size="sm" />
                  Stiamo registrando la carta…
                </p>
              )}
              {pollScaduto && !metodoPresente && (
                <div className="flex flex-wrap items-center gap-2" role="status">
                  <p className="text-body text-ink-2">
                    Stiamo ancora registrando la carta: aggiorna tra poco.
                  </p>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => management.refetch()}
                  >
                    Ricontrolla
                  </Button>
                </div>
              )}
              {notice && <InlineError>{notice}</InlineError>}
            </div>
          )}
        </div>
      )}

      {/* Conferma disdetta */}
      <ConfirmDialog
        open={confermaDisdetta}
        titolo="Disdire il rinnovo?"
        conferma="Conferma la disdetta"
        inCorso={downgrade.isPending}
        onConferma={handleDisdetta}
        onAnnulla={() => setConfermaDisdetta(false)}
      >
        <p>
          Resterai su <strong className="text-ink">{pianoNome ?? "il tuo piano"}</strong> fino al{" "}
          <strong className="text-ink">{formatDateNumeric(data?.data_scadenza)}</strong>, poi
          passerai a Gratuito. Non perdi nulla del periodo già pagato e puoi annullare la disdetta
          fino a quel giorno.
        </p>
        {downgrade.isError && (
          <InlineError className="mt-3">{apiErrorMessage(downgrade.error)}</InlineError>
        )}
      </ConfirmDialog>

      {/* Conferma rimozione metodo */}
      <ConfirmDialog
        open={confermaRimozione}
        titolo="Rimuovere il metodo di pagamento?"
        conferma="Rimuovi"
        distruttiva
        inCorso={removeMethod.isPending}
        onConferma={handleRimuovi}
        onAnnulla={() => setConfermaRimozione(false)}
      >
        <p>
          Rimuovendo {data?.metodo.label ?? "la carta"} si spegne anche il rinnovo automatico: il
          piano resta attivo fino alla scadenza e non verrà addebitato nulla.
        </p>
        {removeMethod.isError && (
          <InlineError className="mt-3">{apiErrorMessage(removeMethod.error)}</InlineError>
        )}
      </ConfirmDialog>
    </Section>
  );
}
