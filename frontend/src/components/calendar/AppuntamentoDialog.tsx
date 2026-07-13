import { CalendarClock } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAnnullaAppuntamento } from "../../hooks/useProgettistaRichieste";
import { apiErrorMessage } from "../../lib/api";
import { formatSlotGiorno, formatSlotOra } from "../../lib/format";
import type { AppuntamentoProgettista } from "../../types";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";

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
    if (!confirmCancel) {
      setConfirmCancel(true);
      return;
    }
    try {
      await annulla.mutateAsync(appuntamento.id);
      onClose();
    } catch {
      // errore mostrato sotto
    }
  };

  return (
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
            onClick={handleCancel}
            loading={annulla.isPending}
          >
            {confirmCancel ? "Confermi l'annullo?" : "Annulla l'appuntamento"}
          </Button>
          <Button type="button" variant="ghost" onClick={onClose}>
            Chiudi
          </Button>
        </>
      }
    >
      {appuntamento && (
        <div className="space-y-3 text-sm">
          <p className="inline-flex items-center gap-2 font-medium text-slate-900">
            <CalendarClock className="size-4 shrink-0 text-violet-500" aria-hidden />
            <span>
              <span className="capitalize">{formatSlotGiorno(appuntamento.inizio)}</span>
              {", "}
              {formatSlotOra(appuntamento.inizio)} – {formatSlotOra(appuntamento.fine)}
            </span>
          </p>
          <div className="rounded-lg bg-slate-50 px-3 py-2.5">
            <p className="font-medium text-slate-800">
              {appuntamento.ragione_sociale ?? "Azienda"}
            </p>
            <p className="mt-0.5 text-slate-600">{appuntamento.bando_titolo}</p>
            {appuntamento.email && (
              <p className="mt-0.5 text-xs text-slate-500">{appuntamento.email}</p>
            )}
          </div>
          <Link
            to={`/app/progettista/richieste/${appuntamento.request_id}`}
            onClick={onClose}
            className="inline-block font-medium text-brand-600 underline-offset-2 hover:underline"
          >
            Vedi la consulenza →
          </Link>
          {annulla.error && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-red-700" role="alert">
              {apiErrorMessage(annulla.error)}
            </p>
          )}
        </div>
      )}
    </Dialog>
  );
}
