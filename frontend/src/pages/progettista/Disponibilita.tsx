import { CalendarClock, CalendarPlus, Pencil, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Dialog } from "../../components/ui/Dialog";
import { TextField } from "../../components/ui/Field";
import { EmptyState, ErrorState, Skeleton } from "../../components/ui/states";
import {
  useAnnullaAppuntamento,
  useAppuntamenti,
} from "../../hooks/useProgettistaRichieste";
import { useCreateSlot, useDeleteSlot, useSlots, useUpdateSlot } from "../../hooks/useSlots";
import { apiErrorMessage } from "../../lib/api";
import { formatSlotGiorno, formatSlotOra, toLocalIsoDate } from "../../lib/format";
import type { Slot } from "../../types";

/** Gli appuntamenti confermati del progettista, sopra il calendario delle
 *  disponibilità: sono la ragione per cui gli slot esistono. */
function AppuntamentiSection() {
  const { data: appuntamenti } = useAppuntamenti();
  const annulla = useAnnullaAppuntamento();
  const [cancelError, setCancelError] = useState<string | null>(null);

  if (!appuntamenti || appuntamenti.length === 0) return null;

  return (
    <section className="mt-6" aria-label="Appuntamenti confermati">
      <h2 className="font-display text-lg font-bold tracking-tight text-slate-900">
        Appuntamenti
      </h2>
      <Card className="mt-2 divide-y divide-slate-100">
        {appuntamenti.map((appuntamento) => (
          <div
            key={appuntamento.id}
            className="flex flex-wrap items-center justify-between gap-2 px-4 py-3"
          >
            <div className="min-w-0">
              <p className="inline-flex items-center gap-2 text-sm font-medium text-slate-900">
                <CalendarClock className="size-4 shrink-0 text-brand-500" aria-hidden />
                <span>
                  <span className="capitalize">{formatSlotGiorno(appuntamento.inizio)}</span>,{" "}
                  {formatSlotOra(appuntamento.inizio)} – {formatSlotOra(appuntamento.fine)}
                </span>
              </p>
              <p className="mt-0.5 text-xs text-slate-500">
                {appuntamento.ragione_sociale ?? "Azienda"} · {appuntamento.bando_titolo}
              </p>
            </div>
            <div className="flex items-center gap-1.5">
              <Link
                to={`/app/progettista/richieste/${appuntamento.request_id}`}
                className="text-sm font-medium text-brand-600 underline-offset-2 hover:underline"
              >
                Vedi la consulenza
              </Link>
              <Button
                variant="ghost"
                size="sm"
                className="text-red-600 hover:bg-red-50 hover:text-red-700"
                loading={annulla.isPending}
                onClick={async () => {
                  setCancelError(null);
                  try {
                    await annulla.mutateAsync(appuntamento.id);
                  } catch (err) {
                    setCancelError(apiErrorMessage(err));
                  }
                }}
              >
                Annulla
              </Button>
            </div>
          </div>
        ))}
      </Card>
      {cancelError && (
        <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {cancelError}
        </p>
      )}
    </section>
  );
}

const pad = (n: number) => String(n).padStart(2, "0");
const toLocalTime = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;

interface FormState {
  slot: Slot | null; // null = creazione
  data: string;
  oraInizio: string;
  oraFine: string;
}

/** Valori iniziali per un nuovo slot: domani alle 10:00, durata 30 minuti
 *  (la durata dell'addon «Consulto esperto»; resta libera). */
function defaultForm(): FormState {
  const domani = new Date();
  domani.setDate(domani.getDate() + 1);
  return { slot: null, data: toLocalIsoDate(domani), oraInizio: "10:00", oraFine: "10:30" };
}

function formFromSlot(slot: Slot): FormState {
  const inizio = new Date(slot.inizio);
  const fine = new Date(slot.fine);
  return {
    slot,
    data: toLocalIsoDate(inizio),
    oraInizio: toLocalTime(inizio),
    oraFine: toLocalTime(fine),
  };
}

export default function Disponibilita() {
  const { data: slots, isPending, isError, error, refetch } = useSlots();
  const createSlot = useCreateSlot();
  const updateSlot = useUpdateSlot();
  const deleteSlot = useDeleteSlot();

  const [form, setForm] = useState<FormState | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<Slot | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const busy = createSlot.isPending || updateSlot.isPending;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form || busy) return;
    setFormError(null);
    // Gli input sono nel fuso del browser: toISOString li porta in UTC.
    const inizio = new Date(`${form.data}T${form.oraInizio}`);
    const fine = new Date(`${form.data}T${form.oraFine}`);
    if (Number.isNaN(inizio.getTime()) || Number.isNaN(fine.getTime())) {
      setFormError("Compila data e orari dello slot.");
      return;
    }
    const payload = { inizio: inizio.toISOString(), fine: fine.toISOString() };
    try {
      if (form.slot) {
        await updateSlot.mutateAsync({ slotId: form.slot.id, ...payload });
      } else {
        await createSlot.mutateAsync(payload);
      }
      setForm(null);
    } catch (err) {
      setFormError(apiErrorMessage(err));
    }
  };

  const handleDelete = async () => {
    if (!deleting || deleteSlot.isPending) return;
    setDeleteError(null);
    try {
      await deleteSlot.mutateAsync(deleting.id);
      setDeleting(null);
    } catch (err) {
      setDeleteError(apiErrorMessage(err));
    }
  };

  // Slot raggruppati per giorno (nel fuso del browser), già ordinati dal server.
  const gruppi = (slots ?? []).reduce<Array<{ giorno: string; slots: Slot[] }>>(
    (acc, slot) => {
      const giorno = formatSlotGiorno(slot.inizio);
      const ultimo = acc[acc.length - 1];
      if (ultimo && ultimo.giorno === giorno) ultimo.slots.push(slot);
      else acc.push({ giorno, slots: [slot] });
      return acc;
    },
    [],
  );

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-bold tracking-tight text-slate-900">
            Disponibilità
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Gli slot che crei qui sono prenotabili dai clienti a cui vieni assegnato. Gli
            orari sono mostrati nel tuo fuso orario.
          </p>
        </div>
        <Button
          onClick={() => {
            setFormError(null);
            setForm(defaultForm());
          }}
        >
          <CalendarPlus className="size-4" aria-hidden />
          Nuovo slot
        </Button>
      </div>

      <AppuntamentiSection />

      <div className="mt-6">
        {isPending ? (
          <div className="space-y-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full" />
            ))}
          </div>
        ) : isError ? (
          <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
        ) : gruppi.length === 0 ? (
          <EmptyState
            title="Nessuna disponibilità"
            description="Crea il tuo primo slot: i clienti delle consulenze che ti verranno assegnate potranno prenotare in questi orari."
          />
        ) : (
          <div className="space-y-6">
            {gruppi.map((gruppo) => (
              <section key={gruppo.giorno}>
                <h2 className="text-sm font-semibold capitalize text-slate-700">
                  {gruppo.giorno}
                </h2>
                <Card className="mt-2 divide-y divide-slate-100">
                  {gruppo.slots.map((slot) => (
                    <div
                      key={slot.id}
                      className="flex flex-wrap items-center justify-between gap-2 px-4 py-3"
                    >
                      <div className="flex items-center gap-3">
                        <span className="tabular text-sm font-medium text-slate-900">
                          {formatSlotOra(slot.inizio)} – {formatSlotOra(slot.fine)}
                        </span>
                        {slot.prenotato ? (
                          <Badge tone="brand">Prenotato</Badge>
                        ) : (
                          <Badge tone="slate">Libero</Badge>
                        )}
                      </div>
                      <div className="flex gap-1.5">
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={slot.prenotato}
                          title={
                            slot.prenotato
                              ? "Lo slot è prenotato: non può essere modificato"
                              : undefined
                          }
                          onClick={() => {
                            setFormError(null);
                            setForm(formFromSlot(slot));
                          }}
                        >
                          <Pencil className="size-3.5" aria-hidden />
                          Modifica
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={slot.prenotato}
                          title={
                            slot.prenotato
                              ? "Lo slot è prenotato: non può essere eliminato"
                              : undefined
                          }
                          className="text-red-600 hover:bg-red-50 hover:text-red-700"
                          onClick={() => {
                            setDeleteError(null);
                            setDeleting(slot);
                          }}
                        >
                          <Trash2 className="size-3.5" aria-hidden />
                          Elimina
                        </Button>
                      </div>
                    </div>
                  ))}
                </Card>
              </section>
            ))}
          </div>
        )}
      </div>

      {/* Dialog di creazione/modifica */}
      <Dialog
        open={!!form}
        onClose={() => setForm(null)}
        dismissible={!busy}
        title={form?.slot ? "Modifica slot" : "Nuovo slot"}
        footer={
          <>
            <Button variant="ghost" onClick={() => setForm(null)} disabled={busy}>
              Annulla
            </Button>
            <Button type="submit" form="slot-form" loading={busy}>
              {form?.slot ? "Salva modifiche" : "Crea slot"}
            </Button>
          </>
        }
      >
        {form && (
          <form id="slot-form" onSubmit={handleSubmit} className="space-y-4">
            <TextField
              label="Data"
              type="date"
              required
              value={form.data}
              min={toLocalIsoDate(new Date())}
              onChange={(e) => setForm({ ...form, data: e.target.value })}
            />
            <div className="grid grid-cols-2 gap-3">
              <TextField
                label="Ora di inizio"
                type="time"
                required
                value={form.oraInizio}
                onChange={(e) => setForm({ ...form, oraInizio: e.target.value })}
              />
              <TextField
                label="Ora di fine"
                type="time"
                required
                value={form.oraFine}
                onChange={(e) => setForm({ ...form, oraFine: e.target.value })}
              />
            </div>
            <p className="text-sm text-slate-500">
              Il consulto dell'addon dura 30 minuti, ma la durata dello slot è libera (da
              15 minuti a 12 ore).
            </p>
            {formError && (
              <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
                {formError}
              </p>
            )}
          </form>
        )}
      </Dialog>

      {/* Dialog di conferma eliminazione */}
      <Dialog
        open={!!deleting}
        onClose={() => setDeleting(null)}
        title="Eliminare lo slot?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              Annulla
            </Button>
            <Button variant="danger" onClick={handleDelete} loading={deleteSlot.isPending}>
              Elimina
            </Button>
          </>
        }
      >
        {deleting && (
          <p>
            Lo slot del{" "}
            <strong className="text-slate-900">
              {formatSlotGiorno(deleting.inizio)}, {formatSlotOra(deleting.inizio)} –{" "}
              {formatSlotOra(deleting.fine)}
            </strong>{" "}
            non sarà più prenotabile.
          </p>
        )}
        {deleteError && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-red-700" role="alert">
            {deleteError}
          </p>
        )}
      </Dialog>
    </div>
  );
}
