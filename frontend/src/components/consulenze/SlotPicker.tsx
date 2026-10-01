import { Check } from "lucide-react";
import { useEffect, useState } from "react";
import { useSlotDisponibili } from "../../hooks/useConsulenze";
import { cn } from "../../lib/cn";
import { CONSULENZE_COPY } from "../../lib/copy";
import { formatSlotGiorno, formatSlotOra } from "../../lib/format";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { InlineError } from "../ui/InlineError";
import { Skeleton } from "../ui/states";
import { conIniziale } from "./formato";
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

  // Reset a ogni apertura: il picker di prenotazione resta montato tra un
  // uso e l'altro, e uno slot selezionato in una sessione precedente
  // (magari nel frattempo eliminato o prenotato) resterebbe inviabile.
  useEffect(() => {
    if (open) setSelected(null);
  }, [open]);

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
          <Button type="button" variant="secondary" onClick={onClose} disabled={busy}>
            Annulla
          </Button>
          <Button
            type="button"
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
        <div className="flex flex-col gap-2" aria-hidden>
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
        </div>
      ) : gruppi.length === 0 ? (
        <p className="text-body text-ink-2">
          Il progettista non ha slot liberi al momento.
          {allowSkip && " Puoi comunque procedere: l'appuntamento si prenota anche dopo."}
        </p>
      ) : (
        <fieldset>
          <legend className="text-body text-ink-2">
            Scegli un orario. {CONSULENZE_COPY.fusoOrario}
          </legend>
          <div className="mt-3 flex max-h-72 flex-col gap-4 overflow-y-auto pr-1">
            {gruppi.map((gruppo) => (
              <div key={gruppo.giorno} className="flex flex-col gap-2">
                <p className="text-small font-medium text-ink-2">{conIniziale(gruppo.giorno)}</p>
                <div className="flex flex-wrap gap-2">
                  {gruppo.slots.map((slot) => {
                    const attivo = selected === slot.id;
                    return (
                      <button
                        key={slot.id}
                        type="button"
                        aria-pressed={attivo}
                        onClick={() => setSelected(attivo ? null : slot.id)}
                        className={cn(
                          "inline-flex h-9 cursor-pointer items-center gap-1.5 rounded-control border px-3 text-body font-medium tabular-nums transition-colors duration-150 ease-uscita",
                          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
                          attivo
                            ? "border-accent bg-accent-soft text-ink"
                            : "border-line-control bg-sheet text-ink hover:bg-sunken",
                        )}
                      >
                        {/* Il segno non cromatico della scelta: non conta solo il colore. */}
                        {attivo && <Check className="size-4 text-accent" aria-hidden />}
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
      {error && <InlineError className="mt-3">{error}</InlineError>}
    </Dialog>
  );
}
