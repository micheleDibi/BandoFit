import { Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import {
  useCreateEvent,
  useDeleteEvent,
  useUpdateEvent,
  type CalendarEventPayload,
} from "../../hooks/useCalendar";
import { apiErrorMessage } from "../../lib/api";
import { formatDate } from "../../lib/format";
import type { CalendarEvent } from "../../types";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Checkbox } from "../ui/Checkbox";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Dialog } from "../ui/Dialog";
import { TextareaField, TextField } from "../ui/Field";
import { InlineError } from "../ui/InlineError";
import { TextLink } from "../ui/TextLink";
import { useToast } from "../ui/Toast";

export type DialogState =
  | { mode: "create"; date: string }
  | { mode: "edit"; event: CalendarEvent }
  | null;

interface FormState {
  titolo: string;
  data: string;
  tuttoIlGiorno: boolean;
  oraInizio: string;
  oraFine: string;
  note: string;
}

function initialForm(state: Exclude<DialogState, null>): FormState {
  if (state.mode === "create") {
    return { titolo: "", data: state.date, tuttoIlGiorno: true, oraInizio: "", oraFine: "", note: "" };
  }
  const e = state.event;
  return {
    titolo: e.titolo,
    data: e.data,
    tuttoIlGiorno: e.tutto_il_giorno,
    oraInizio: e.ora_inizio ? e.ora_inizio.slice(0, 5) : "",
    oraFine: e.ora_fine ? e.ora_fine.slice(0, 5) : "",
    note: e.note ?? "",
  };
}

/** Dialog di creazione/modifica evento. Per gli eventi legati a un bando la
 *  data è la scadenza ufficiale: si mostrano solo titolo e note modificabili. */
export function EventDialog({ state, onClose }: { state: DialogState; onClose: () => void }) {
  const createEvent = useCreateEvent();
  const updateEvent = useUpdateEvent();
  const deleteEvent = useDeleteEvent();
  const { mostra } = useToast();

  const [form, setForm] = useState<FormState>({
    titolo: "", data: "", tuttoIlGiorno: true, oraInizio: "", oraFine: "", note: "",
  });
  const [validationError, setValidationError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const open = state !== null;
  const editing = state?.mode === "edit" ? state.event : null;
  const isBando = editing?.tipo === "bando";

  useEffect(() => {
    if (state) {
      setForm(initialForm(state));
      setValidationError(null);
      setConfirmDelete(false);
      createEvent.reset();
      updateEvent.reset();
      deleteEvent.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);

  if (!open || !state) return null;

  const busy = createEvent.isPending || updateEvent.isPending || deleteEvent.isPending;

  const validate = (): string | null => {
    if (!form.titolo.trim()) return "Il titolo è obbligatorio.";
    if (!isBando && !form.data) return "Scegli una data.";
    // Stessi limiti del backend/della vista: una data fuori intervallo
    // creerebbe un evento invisibile dal calendario.
    if (!isBando && (form.data < "2000-01-01" || form.data > "2100-12-31"))
      return "La data deve essere compresa tra il 2000 e il 2100.";
    if (!form.tuttoIlGiorno) {
      if (!form.oraInizio) return "Indica l'ora di inizio (o segna «Tutto il giorno»).";
      if (form.oraFine && form.oraFine <= form.oraInizio)
        return "L'ora di fine deve essere successiva a quella di inizio.";
    }
    return null;
  };

  const handleSubmit = async (e?: FormEvent) => {
    e?.preventDefault();
    const problem = validate();
    setValidationError(problem);
    if (problem) return;

    try {
      if (editing) {
        // Per gli eventi bando il server accetta solo titolo e note.
        const patch: Partial<CalendarEventPayload> = isBando
          ? { titolo: form.titolo.trim(), note: form.note.trim() || null }
          : {
              titolo: form.titolo.trim(),
              data: form.data,
              tutto_il_giorno: form.tuttoIlGiorno,
              ora_inizio: form.tuttoIlGiorno || !form.oraInizio ? null : form.oraInizio,
              ora_fine: form.tuttoIlGiorno || !form.oraFine ? null : form.oraFine,
              note: form.note.trim() || null,
            };
        await updateEvent.mutateAsync({ id: editing.id, patch });
        mostra({ testo: "Evento salvato" });
      } else {
        await createEvent.mutateAsync({
          titolo: form.titolo.trim(),
          data: form.data,
          tutto_il_giorno: form.tuttoIlGiorno,
          ora_inizio: form.tuttoIlGiorno || !form.oraInizio ? null : form.oraInizio,
          ora_fine: form.tuttoIlGiorno || !form.oraFine ? null : form.oraFine,
          note: form.note.trim() || null,
        });
        mostra({ testo: "Evento aggiunto" });
      }
      onClose();
    } catch {
      // errore mostrato sotto
    }
  };

  const handleDelete = async () => {
    if (!editing) return;
    try {
      await deleteEvent.mutateAsync(editing.id);
      setConfirmDelete(false);
      onClose();
      mostra({ testo: "Evento eliminato" });
    } catch {
      // errore mostrato nella conferma
    }
  };

  const mutationError = createEvent.error ?? updateEvent.error;

  // La conferma dell'eliminazione è una SORELLA della finestra, non una figlia:
  // React propaga `close`/`cancel` lungo l'albero dei componenti, e chiudendo
  // la conferma chiuderebbe anche la finestra sotto.
  return (
    <>
      <Dialog
        open={open}
        onClose={onClose}
        title={editing ? "Modifica evento" : "Nuovo evento"}
        footer={
          <>
            {editing && (
              <Button
                type="button"
                variant="danger"
                onClick={() => {
                  deleteEvent.reset();
                  setConfirmDelete(true);
                }}
                disabled={busy}
                className="mr-auto"
              >
                <Trash2 className="size-4" aria-hidden />
                Elimina
              </Button>
            )}
            <Button type="button" variant="secondary" onClick={onClose}>
              Annulla
            </Button>
            {/* onClick esplicito, non l'associazione `form=` (fragile in Safari
                con il bottone fuori dal form dentro un <dialog> modale). */}
            <Button type="button" onClick={() => handleSubmit()} loading={busy && !deleteEvent.isPending}>
              Salva
            </Button>
          </>
        }
      >
        <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
          <TextField
            label="Titolo"
            required
            maxLength={200}
            value={form.titolo}
            onChange={(e) => setForm((f) => ({ ...f, titolo: e.target.value }))}
          />

          {isBando ? (
            <Alert
              tono="info"
              azione={
                editing?.bando_slug && (
                  <TextLink
                    to={`/app/bandi/${editing.bando_slug}`}
                    onClick={onClose}
                    className="text-small font-medium"
                  >
                    Apri il bando
                  </TextLink>
                )
              }
            >
              Scadenza del bando: <strong>{formatDate(form.data)}</strong>. La data non è
              modificabile perché deriva dal bando ufficiale.
            </Alert>
          ) : (
            <>
              <TextField
                label="Data"
                type="date"
                required
                min="2000-01-01"
                max="2100-12-31"
                value={form.data}
                onChange={(e) => setForm((f) => ({ ...f, data: e.target.value }))}
              />
              <Checkbox
                label="Tutto il giorno"
                checked={form.tuttoIlGiorno}
                onChange={(e) => setForm((f) => ({ ...f, tuttoIlGiorno: e.target.checked }))}
              />
              {!form.tuttoIlGiorno && (
                <div className="grid grid-cols-2 gap-4">
                  <TextField
                    label="Ora di inizio"
                    type="time"
                    required
                    value={form.oraInizio}
                    onChange={(e) => setForm((f) => ({ ...f, oraInizio: e.target.value }))}
                  />
                  <TextField
                    label="Ora di fine"
                    type="time"
                    helper="Opzionale"
                    value={form.oraFine}
                    onChange={(e) => setForm((f) => ({ ...f, oraFine: e.target.value }))}
                  />
                </div>
              )}
            </>
          )}

          <TextareaField
            label="Note"
            maxLength={2000}
            rows={3}
            placeholder="Appunti, promemoria…"
            value={form.note}
            onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))}
          />

          {(validationError || mutationError) && (
            <InlineError>{validationError ?? apiErrorMessage(mutationError)}</InlineError>
          )}
        </form>
      </Dialog>
      <ConfirmDialog
        open={confirmDelete}
        titolo="Eliminare l'evento?"
        conferma="Elimina"
        distruttiva
        inCorso={deleteEvent.isPending}
        onConferma={handleDelete}
        onAnnulla={() => setConfirmDelete(false)}
      >
        <p>
          <strong className="text-ink">{form.titolo || editing?.titolo}</strong> sparisce dal
          calendario.
        </p>
        {deleteEvent.isError && (
          <InlineError className="mt-3">{apiErrorMessage(deleteEvent.error)}</InlineError>
        )}
      </ConfirmDialog>
    </>
  );
}
