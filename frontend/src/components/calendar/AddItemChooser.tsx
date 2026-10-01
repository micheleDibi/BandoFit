import { CalendarClock, CalendarPlus } from "lucide-react";
import { formatWeekdayLong } from "../../lib/format";
import { Dialog } from "../ui/Dialog";

const choiceClasses =
  "flex w-full cursor-pointer flex-col gap-0.5 rounded-control border border-line-control bg-sheet px-4 py-3 " +
  "text-left transition-colors hover:bg-desk " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

/** Scelta per i PROGETTISTI al click su un giorno: evento personale o slot di
 *  disponibilità (gli altri utenti aprono direttamente il form evento). */
export function AddItemChooser({
  date,
  onClose,
  onEvento,
  onSlot,
}: {
  date: string | null; // YYYY-MM-DD, null = chiuso
  onClose: () => void;
  onEvento: (date: string) => void;
  onSlot: (date: string) => void;
}) {
  return (
    <Dialog
      open={date !== null}
      onClose={onClose}
      title={date ? formatWeekdayLong(date) : ""}
    >
      <div className="flex flex-col gap-3">
        <p>Che cosa vuoi aggiungere?</p>
        <button type="button" className={choiceClasses} onClick={() => date && onEvento(date)}>
          <span className="inline-flex items-center gap-2 text-title-group text-ink">
            <CalendarPlus className="size-4 text-ink-2" aria-hidden />
            Evento personale
          </span>
          <span className="text-small text-ink-3">
            Un promemoria sul tuo calendario, visibile solo a te.
          </span>
        </button>
        <button type="button" className={choiceClasses} onClick={() => date && onSlot(date)}>
          <span className="inline-flex items-center gap-2 text-title-group text-ink">
            <CalendarClock className="size-4 text-ink-2" aria-hidden />
            Slot di disponibilità
          </span>
          <span className="text-small text-ink-3">
            Prenotabile dai clienti delle consulenze che ti vengono assegnate.
          </span>
        </button>
      </div>
    </Dialog>
  );
}
