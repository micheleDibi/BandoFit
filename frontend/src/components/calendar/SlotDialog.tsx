import { Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import {
  useCreateSlot,
  useCreateSlotSerie,
  useDeleteSlot,
  useDeleteSlotSerie,
  useUpdateSlot,
} from "../../hooks/useSlots";
import { apiErrorMessage } from "../../lib/api";
import { CONSULENZE_COPY } from "../../lib/copy";
import { toLocalIsoDate } from "../../lib/format";
import {
  espandiRicorrenza,
  maxFinoAl,
  type Frequenza,
} from "../../lib/ricorrenza";
import type { Slot } from "../../types";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { SelectField, TextField } from "../ui/Field";

export type SlotDialogState =
  | { mode: "create"; date: string }
  | { mode: "edit"; slot: Slot }
  | null;

interface FormState {
  data: string;
  oraInizio: string;
  oraFine: string;
  frequenza: Frequenza; // solo in creazione; in modifica resta "nessuna"
  finoAl: string;
}

const FREQUENZE: Array<{ value: Frequenza; label: string }> = [
  { value: "nessuna", label: "Non si ripete" },
  { value: "giornaliera", label: "Ogni giorno" },
  { value: "feriale", label: "Ogni giorno feriale (lun–ven)" },
  { value: "settimanale", label: "Ogni settimana" },
  { value: "mensile", label: "Ogni mese" },
];

const pad = (n: number) => String(n).padStart(2, "0");
const toLocalTime = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;

function initialForm(state: Exclude<SlotDialogState, null>): FormState {
  if (state.mode === "create") {
    // Orari default 10:00–10:30 (la durata dell'addon «Consulto esperto»);
    // la data viene dal giorno cliccato sul calendario.
    return { data: state.date, oraInizio: "10:00", oraFine: "10:30", frequenza: "nessuna", finoAl: "" };
  }
  const inizio = new Date(state.slot.inizio);
  const fine = new Date(state.slot.fine);
  return {
    data: toLocalIsoDate(inizio),
    oraInizio: toLocalTime(inizio),
    oraFine: toLocalTime(fine),
    frequenza: "nessuna",
    finoAl: "",
  };
}

/** Creazione (con ripetizione opzionale) e modifica di uno slot di
 *  disponibilità, dal calendario. Gli input sono nel fuso del browser e
 *  viaggiano in UTC; la ricorrenza viene espansa qui (lib/ricorrenza.ts). */
export function SlotDialog({ state, onClose }: { state: SlotDialogState; onClose: () => void }) {
  const createSlot = useCreateSlot();
  const createSerie = useCreateSlotSerie();
  const updateSlot = useUpdateSlot();
  const deleteSlot = useDeleteSlot();
  const deleteSerie = useDeleteSlotSerie();

  const [form, setForm] = useState<FormState>({
    data: "", oraInizio: "", oraFine: "", frequenza: "nessuna", finoAl: "",
  });
  const [validationError, setValidationError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmDeleteSerie, setConfirmDeleteSerie] = useState(false);
  // Vista di esito post-operazione (slot saltati/mantenuti): sostituisce il form.
  const [esito, setEsito] = useState<string | null>(null);

  const open = state !== null;
  const editing = state?.mode === "edit" ? state.slot : null;

  useEffect(() => {
    if (state) {
      setForm(initialForm(state));
      setValidationError(null);
      setConfirmDelete(false);
      setConfirmDeleteSerie(false);
      setEsito(null);
      createSlot.reset();
      createSerie.reset();
      updateSlot.reset();
      deleteSlot.reset();
      deleteSerie.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);

  if (!open || !state) return null;

  const busy =
    createSlot.isPending ||
    createSerie.isPending ||
    updateSlot.isPending ||
    deleteSlot.isPending ||
    deleteSerie.isPending;
  const ripete = !editing && form.frequenza !== "nessuna";

  const validate = (): string | null => {
    if (!form.data || !form.oraInizio || !form.oraFine)
      return "Compila data e orari dello slot.";
    if (form.oraFine <= form.oraInizio)
      return "L'ora di fine deve essere successiva a quella di inizio.";
    if (ripete) {
      if (!form.finoAl) return "Indica fino a quando ripetere lo slot.";
      if (form.finoAl < form.data)
        return "La data di fine ripetizione deve seguire quella del primo slot.";
      if (form.finoAl > maxFinoAl(form.data))
        return "La ripetizione può durare al massimo 12 mesi.";
    }
    return null;
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    const problem = validate();
    setValidationError(problem);
    if (problem) return;

    try {
      if (editing) {
        const inizio = new Date(`${form.data}T${form.oraInizio}`);
        const fine = new Date(`${form.data}T${form.oraFine}`);
        await updateSlot.mutateAsync({
          slotId: editing.id,
          inizio: inizio.toISOString(),
          fine: fine.toISOString(),
        });
        onClose();
      } else if (ripete) {
        const occorrenze = espandiRicorrenza({
          data: form.data,
          oraInizio: form.oraInizio,
          oraFine: form.oraFine,
          frequenza: form.frequenza as Exclude<Frequenza, "nessuna">,
          finoAl: form.finoAl,
        });
        if (occorrenze.length === 0) {
          setValidationError(
            "Tutti gli slot della serie sarebbero nel passato: scegli un orario futuro.",
          );
          return;
        }
        const result = await createSerie.mutateAsync({ occorrenze });
        if (result.saltati > 0) {
          setEsito(
            `Slot creati: ${result.creati.length}. ` +
              `Saltati perché sovrapposti a disponibilità esistenti: ${result.saltati}.`,
          );
        } else {
          onClose();
        }
      } else {
        const inizio = new Date(`${form.data}T${form.oraInizio}`);
        const fine = new Date(`${form.data}T${form.oraFine}`);
        await createSlot.mutateAsync({
          inizio: inizio.toISOString(),
          fine: fine.toISOString(),
        });
        onClose();
      }
    } catch {
      // errore mostrato sotto
    }
  };

  const handleDelete = async () => {
    if (!editing || busy) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      setConfirmDeleteSerie(false);
      return;
    }
    try {
      await deleteSlot.mutateAsync(editing.id);
      onClose();
    } catch {
      // errore mostrato sotto
    }
  };

  const handleDeleteSerie = async () => {
    if (!editing?.serie_id || busy) return;
    if (!confirmDeleteSerie) {
      setConfirmDeleteSerie(true);
      setConfirmDelete(false);
      return;
    }
    try {
      const result = await deleteSerie.mutateAsync(editing.serie_id);
      if (result.mantenuti > 0) {
        setEsito(
          `Slot della serie eliminati: ${result.eliminati}. ` +
            `Mantenuti perché prenotati: ${result.mantenuti}.`,
        );
      } else {
        onClose();
      }
    } catch {
      // errore mostrato sotto
    }
  };

  const mutationError =
    createSlot.error ?? createSerie.error ?? updateSlot.error ??
    deleteSlot.error ?? deleteSerie.error;

  // Esito con numeri da comunicare: il form lascia il posto al riepilogo.
  if (esito) {
    return (
      <Dialog
        open
        onClose={onClose}
        title={editing ? "Serie eliminata" : "Slot creati"}
        footer={<Button onClick={onClose}>Chiudi</Button>}
      >
        <p className="text-sm text-slate-700">{esito}</p>
      </Dialog>
    );
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      dismissible={!busy}
      title={editing ? "Modifica slot" : "Nuovo slot di disponibilità"}
      footer={
        <>
          {editing && (
            <div className="mr-auto flex flex-wrap gap-2">
              <Button
                type="button"
                variant="danger"
                onClick={handleDelete}
                loading={deleteSlot.isPending}
              >
                <Trash2 className="size-4" aria-hidden />
                {confirmDelete ? "Confermi?" : "Elimina"}
              </Button>
              {editing.serie_id && (
                <Button
                  type="button"
                  variant="ghost"
                  className="text-red-600 hover:bg-red-50 hover:text-red-700"
                  onClick={handleDeleteSerie}
                  loading={deleteSerie.isPending}
                >
                  {confirmDeleteSerie ? "Confermi la serie?" : "Elimina la serie"}
                </Button>
              )}
            </div>
          )}
          <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>
            Annulla
          </Button>
          <Button
            type="submit"
            form="slot-form"
            loading={busy && !deleteSlot.isPending && !deleteSerie.isPending}
          >
            {editing ? "Salva modifiche" : "Crea"}
          </Button>
        </>
      }
    >
      <form id="slot-form" onSubmit={handleSubmit} className="space-y-4">
        <TextField
          label="Data"
          type="date"
          required
          value={form.data}
          min={toLocalIsoDate(new Date())}
          onChange={(e) => setForm((f) => ({ ...f, data: e.target.value }))}
        />
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
            required
            value={form.oraFine}
            onChange={(e) => setForm((f) => ({ ...f, oraFine: e.target.value }))}
          />
        </div>

        {!editing && (
          <>
            <SelectField
              label="Ripetizione"
              value={form.frequenza}
              onChange={(e) =>
                setForm((f) => ({ ...f, frequenza: e.target.value as Frequenza }))
              }
            >
              {FREQUENZE.map((opzione) => (
                <option key={opzione.value} value={opzione.value}>
                  {opzione.label}
                </option>
              ))}
            </SelectField>
            {ripete && (
              <TextField
                label="Ripeti fino al"
                type="date"
                required
                value={form.finoAl}
                min={form.data}
                max={form.data ? maxFinoAl(form.data) : undefined}
                helper="Al massimo 12 mesi. Gli slot che si sovrappongono a disponibilità già esistenti verranno saltati."
                onChange={(e) => setForm((f) => ({ ...f, finoAl: e.target.value }))}
              />
            )}
          </>
        )}

        {editing?.serie_id && (
          <p className="text-sm text-slate-500">
            Questo slot fa parte di una serie ricorrente. «Elimina la serie» rimuove
            tutti i suoi slot, anche quelli modificati singolarmente; quelli prenotati
            restano.
          </p>
        )}

        <p className="text-sm text-slate-500">
          Il consulto dell'addon dura 30 minuti, ma la durata dello slot è libera (da
          15 minuti a 12 ore). {CONSULENZE_COPY.fusoOrario}
        </p>

        {(validationError || mutationError) && (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {validationError ?? apiErrorMessage(mutationError)}
          </p>
        )}
      </form>
    </Dialog>
  );
}
