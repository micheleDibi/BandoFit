import { CalendarClock } from "lucide-react";
import { useEffect, useState } from "react";
import { useAnnullaAppuntamento } from "../../hooks/useProgettistaRichieste";
import { VideocallButton } from "../consulenze/VideocallButton";
import { apiErrorMessage } from "../../lib/api";
import { formatSlotGiorno, formatSlotOra } from "../../lib/format";
import type { AppuntamentoProgettista } from "../../types";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Dialog } from "../ui/Dialog";
import { InlineError } from "../ui/InlineError";
import { TextLink } from "../ui/TextLink";
import { useToast } from "../ui/Toast";

/** Dettaglio di un appuntamento confermato, dal calendario del progettista:
 *  link alla consulenza e annullo (che libera lo slot da solo). */
export function AppuntamentoDialog({
  appuntamento,
  onClose,
}: {
  appuntamento: AppuntamentoProgettista | null;
  onClose: () => void;
}) {
  const annulla = useAnnullaAppuntamento();
  const { mostra } = useToast();
  const [confirmCancel, setConfirmCancel] = useState(false);

  useEffect(() => {
    if (appuntamento) {
      setConfirmCancel(false);
      annulla.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appuntamento]);

  const handleCancel = async () => {
    if (!appuntamento || annulla.isPending) return;
    try {
      await annulla.mutateAsync(appuntamento.id);
      setConfirmCancel(false);
      onClose();
      mostra({ testo: "Appuntamento annullato" });
    } catch {
      // errore mostrato nella conferma
    }
  };

  // La conferma è una SORELLA della finestra, non una figlia: React propaga
  // `close`/`cancel` lungo l'albero dei componenti.
  return (
    <>
      <Dialog
        open={appuntamento !== null}
        onClose={onClose}
        dismissible={!annulla.isPending}
        title="Appuntamento"
        footer={
          <>
            <Button
              type="button"
              variant="danger"
              className="mr-auto"
              onClick={() => {
                annulla.reset();
                setConfirmCancel(true);
              }}
              disabled={annulla.isPending}
            >
              Annulla l'appuntamento
            </Button>
            <Button type="button" variant="secondary" onClick={onClose}>
              Chiudi
            </Button>
          </>
        }
      >
        {appuntamento && (
          <div className="flex flex-col gap-3">
            <p className="inline-flex items-center gap-2 font-medium text-ink">
              <CalendarClock className="size-4 shrink-0 text-ink-2" aria-hidden />
              <span>
                <span className="capitalize">{formatSlotGiorno(appuntamento.inizio)}</span>
                {", "}
                <span className="tabular-nums">
                  {formatSlotOra(appuntamento.inizio)}–{formatSlotOra(appuntamento.fine)}
                </span>
              </span>
            </p>
            <div className="flex flex-col gap-0.5 rounded-control bg-desk px-3 py-2.5">
              <p className="font-semibold text-ink">{appuntamento.ragione_sociale ?? "Azienda"}</p>
              <p className="text-ink-2">{appuntamento.bando_titolo}</p>
              {appuntamento.email && (
                <p className="text-small text-ink-3">{appuntamento.email}</p>
              )}
            </div>
            {appuntamento.videocall_url && <VideocallButton url={appuntamento.videocall_url} />}
            <TextLink
              to={`/app/progettista/richieste/${appuntamento.request_id}`}
              onClick={onClose}
              className="self-start font-medium"
            >
              Vedi la consulenza
            </TextLink>
          </div>
        )}
      </Dialog>
      <ConfirmDialog
        open={confirmCancel}
        titolo="Annullare l'appuntamento?"
        conferma="Annulla l'appuntamento"
        annulla="Indietro"
        distruttiva
        inCorso={annulla.isPending}
        onConferma={handleCancel}
        onAnnulla={() => setConfirmCancel(false)}
      >
        <p>Lo slot torna disponibile per le prenotazioni.</p>
        {annulla.isError && (
          <InlineError className="mt-3">{apiErrorMessage(annulla.error)}</InlineError>
        )}
      </ConfirmDialog>
    </>
  );
}
