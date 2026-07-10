import { useState } from "react";
import { useSlotDisponibili } from "../../hooks/useConsulenze";
import { cn } from "../../lib/cn";
import { CONSULENZE_COPY } from "../../lib/copy";
import { formatSlotGiorno, formatSlotOra } from "../../lib/format";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Skeleton } from "../ui/states";
import type { Slot } from "../../types";

/** Scelta di uno slot libero del progettista. Due usi:
 *  - accettazione di una proposta (slot opzionale: «anche senza appuntamento»)
 *  - prenotazione dopo l'assegnazione (slot obbligatorio). */
export function SlotPicker({
  open,
  onClose,
  requestId,
  propostaId,
  title,
  confirmLabel,
  allowSkip,
  busy,
  error,
  onConfirm,
}: {
  open: boolean;
  onClose: () => void;
  requestId: string;
  /** null = slot del progettista già assegnato. */
  propostaId: string | null;
  title: string;
  confirmLabel: string;
  /** true = si può confermare senza scegliere uno slot (accettazione). */
  allowSkip: boolean;
  busy: boolean;
  error: string | null;
  onConfirm: (slotId: string | null) => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const { data: slots, isPending } = useSlotDisponibili(requestId, propostaId, open);

  // Slot raggruppati per giorno nel fuso del browser.
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
    <Dialog
      open={open}
      onClose={onClose}
      dismissible={!busy}
      title={title}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Annulla
          </Button>
          <Button
            loading={busy}
            disabled={!allowSkip && !selected}
            onClick={() => onConfirm(selected)}
          >
            {selected || !allowSkip ? confirmLabel : `${confirmLabel} senza appuntamento`}
          </Button>
        </>
      }
    >
      {isPending ? (
        <div className="space-y-2">
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
        </div>
      ) : gruppi.length === 0 ? (
        <p className="text-sm text-slate-600">
          Il progettista non ha slot liberi al momento.
          {allowSkip && " Puoi comunque procedere: l'appuntamento si prenota anche dopo."}
        </p>
      ) : (
        <fieldset>
          <legend className="text-sm text-slate-600">
            Scegli un orario. {CONSULENZE_COPY.fusoOrario}
          </legend>
          <div className="mt-3 max-h-72 space-y-4 overflow-y-auto pr-1">
            {gruppi.map((gruppo) => (
              <div key={gruppo.giorno}>
                <p className="text-xs font-semibold capitalize text-slate-500">
                  {gruppo.giorno}
                </p>
                <div className="mt-1.5 flex flex-wrap gap-2">
                  {gruppo.slots.map((slot) => {
                    const attivo = selected === slot.id;
                    return (
                      <button
                        key={slot.id}
                        type="button"
                        aria-pressed={attivo}
                        onClick={() => setSelected(attivo ? null : slot.id)}
                        className={cn(
                          "tabular cursor-pointer rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors",
                          attivo
                            ? "border-brand-500 bg-brand-50 text-brand-700"
                            : "border-slate-200 bg-white text-slate-700 hover:border-brand-300 hover:bg-slate-50",
                        )}
                      >
                        {formatSlotOra(slot.inizio)} – {formatSlotOra(slot.fine)}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </fieldset>
      )}
      {error && (
        <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {error}
        </p>
      )}
    </Dialog>
  );
}
