import RevolutCheckout from "@revolut/checkout";
import { CalendarClock, CreditCard, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useCancelScheduledChange, useRemoveMethod, useScheduleDowngrade, useSetAutoRenew, useStartAddMethod, useSubscriptionManagement } from "../../hooks/useSubscriptionManagement";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { formatDateNumeric } from "../../lib/format";
import { REVOLUT_MODE } from "../../lib/revolut";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Dialog } from "../ui/Dialog";
import { Skeleton } from "../ui/states";

// Dopo l'onSuccess del widget il metodo compare via riconciliazione, ma in
// dev il webhook non è garantito: breve polling, poi si passa al messaggio
// «aggiorna tra poco».
const METHOD_POLL_MAX_MS = 20_000;

/** Sezione «Pagamento e rinnovo» di Abbonamento: metodo salvato, rinnovo
 *  automatico, disdetta e cambio programmato. Visibile solo con un piano a
 *  pagamento attivo (o con un cambio già programmato da mostrare). */
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

  // Niente piano a pagamento e niente cambio da mostrare: niente sezione.
  // (Return DOPO tutti gli hook.)
  if (!pianoAPagamento && !data?.cambio_programmato) return null;

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
      // errore mostrato nel dialog
    }
  };

  const handleRimuovi = async () => {
    setNotice(null);
    try {
      await removeMethod.mutateAsync();
      setConfermaRimozione(false);
    } catch {
      // errore mostrato nel dialog
    }
  };

  return (
    <section className="mt-10" aria-label="Pagamento e rinnovo">
      <h2 className="inline-flex items-center gap-2 font-display text-xl font-bold tracking-tight text-slate-900">
        <CreditCard className="size-5 text-brand-500" aria-hidden />
        Pagamento e rinnovo
      </h2>
      <p className="mt-1 text-sm text-slate-500">
        Il metodo di pagamento salvato e come si rinnova il tuo piano.
      </p>

      {management.isError ? (
        <p className="mt-4 max-w-2xl rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">
          {apiErrorMessage(management.error)}{" "}
          <button
            type="button"
            onClick={() => management.refetch()}
            className="cursor-pointer font-medium underline underline-offset-2"
          >
            Riprova
          </button>
        </p>
      ) : management.isPending || !data ? (
        <Skeleton className="mt-4 h-48 w-full max-w-2xl" />
      ) : (
        <Card className="mt-4 max-w-2xl divide-y divide-slate-100">
          {/* Cambio programmato: informa e lascia annullare */}
          {data.cambio_programmato && (
            <div className="flex flex-wrap items-center justify-between gap-3 bg-amber-50/60 p-5">
              <p className="text-sm text-amber-800">
                <strong>
                  {data.cambio_programmato.motivo === "disdetta"
                    ? "Disdetta programmata"
                    : "Downgrade programmato"}
                </strong>
                : passerai a {data.cambio_programmato.to_plan_nome} il{" "}
                {formatDateNumeric(data.cambio_programmato.effective_date)}. Fino ad allora resta
                tutto attivo.
              </p>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => annullaCambio.mutate()}
                loading={annullaCambio.isPending}
              >
                Annulla
              </Button>
            </div>
          )}
          {annullaCambio.isError && (
            <p className="px-5 py-3 text-sm text-red-700" role="alert">
              {apiErrorMessage(annullaCambio.error)}
            </p>
          )}

          {/* Metodo di pagamento salvato */}
          <div className="flex flex-wrap items-center justify-between gap-3 p-5">
            <div>
              <p className="text-sm font-medium text-slate-700">Metodo di pagamento</p>
              <p className="mt-0.5 inline-flex items-center gap-1.5 text-sm text-slate-500">
                <CreditCard className="size-4 shrink-0 text-slate-400" aria-hidden />
                {metodoPresente ? (data.metodo.label ?? "Metodo salvato") : "Nessun metodo salvato"}
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => handleAddMethod(false)}
                loading={opening || startAddMethod.isPending}
                disabled={pollMetodo}
              >
                {metodoPresente ? "Sostituisci" : "Aggiungi metodo"}
              </Button>
              {metodoPresente && (
                <Button variant="ghost" size="sm" onClick={() => setConfermaRimozione(true)}>
                  Rimuovi
                </Button>
              )}
            </div>
          </div>

          {/* Rinnovo automatico */}
          {pianoAPagamento && (
            <div className="p-5">
              <label className="flex cursor-pointer items-start gap-2.5">
                <input
                  type="checkbox"
                  className="mt-0.5 size-4 cursor-pointer accent-brand-500"
                  checked={data.auto_renew}
                  disabled={autoRenew.isPending}
                  onChange={(e) => handleToggleRenew(e.target.checked)}
                />
                <span>
                  <span className="block text-sm font-medium text-slate-700">
                    Rinnovo automatico
                  </span>
                  <span className="mt-0.5 block text-xs text-slate-500">
                    Ti avvisiamo via email almeno 7 giorni prima di ogni addebito. Puoi disdire
                    quando vuoi.
                  </span>
                </span>
              </label>

              {/* Disdetta: solo con rinnovo attivo e nessun cambio già programmato */}
              {!data.cambio_programmato &&
                (data.auto_renew ? (
                  <div className="mt-4 border-t border-slate-100 pt-4">
                    <Button variant="secondary" size="sm" onClick={() => setConfermaDisdetta(true)}>
                      <CalendarClock className="size-4" aria-hidden />
                      Disdici il rinnovo
                    </Button>
                  </div>
                ) : (
                  data.data_scadenza && (
                    <p className="mt-3 text-xs text-slate-400">
                      Il piano non si rinnova da solo: resta attivo fino al{" "}
                      {formatDateNumeric(data.data_scadenza)}.
                    </p>
                  )
                ))}
            </div>
          )}

          {/* Area di stato condivisa (salvataggio carta, errori) */}
          {(pollMetodo || (pollScaduto && !metodoPresente) || notice) && (
            <div className="space-y-2 p-5">
              {pollMetodo && (
                <p className="inline-flex items-center gap-2 text-sm text-slate-500" role="status">
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                  Stiamo registrando la carta…
                </p>
              )}
              {pollScaduto && !metodoPresente && (
                <p className="text-sm text-slate-500" role="status">
                  Stiamo ancora registrando la carta: aggiorna tra poco.{" "}
                  <button
                    type="button"
                    onClick={() => management.refetch()}
                    className="cursor-pointer font-medium text-brand-600 underline underline-offset-2"
                  >
                    Ricontrolla
                  </button>
                </p>
              )}
              {notice && (
                <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
                  {notice}
                </p>
              )}
            </div>
          )}
        </Card>
      )}

      {/* Conferma disdetta */}
      <Dialog
        open={confermaDisdetta}
        onClose={() => setConfermaDisdetta(false)}
        title="Disdici il rinnovo"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfermaDisdetta(false)}>
              Annulla
            </Button>
            <Button onClick={handleDisdetta} loading={downgrade.isPending}>
              Conferma la disdetta
            </Button>
          </>
        }
      >
        <p>
          Resterai su <strong className="text-slate-900">{pianoNome ?? "il tuo piano"}</strong>{" "}
          fino al{" "}
          <strong className="text-slate-900">{formatDateNumeric(data?.data_scadenza)}</strong>,
          poi passerai a Gratuito. Non perdi nulla del periodo già pagato e puoi annullare la
          disdetta fino a quel giorno.
        </p>
        {downgrade.isError && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-red-700" role="alert">
            {apiErrorMessage(downgrade.error)}
          </p>
        )}
      </Dialog>

      {/* Conferma rimozione metodo */}
      <Dialog
        open={confermaRimozione}
        onClose={() => setConfermaRimozione(false)}
        title="Rimuovi il metodo di pagamento"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfermaRimozione(false)}>
              Annulla
            </Button>
            <Button variant="danger" onClick={handleRimuovi} loading={removeMethod.isPending}>
              Rimuovi
            </Button>
          </>
        }
      >
        <p>
          Rimuovendo {data?.metodo.label ?? "la carta"} si spegne anche il rinnovo automatico: il
          piano resta attivo fino alla scadenza e non verrà addebitato nulla.
        </p>
        {removeMethod.isError && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-red-700" role="alert">
            {apiErrorMessage(removeMethod.error)}
          </p>
        )}
      </Dialog>
    </section>
  );
}
