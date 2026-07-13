import { CalendarClock, CalendarPlus } from "lucide-react";
import { formatWeekdayLong } from "../../lib/format";
import { Dialog } from "../ui/Dialog";

const choiceClasses =
  "flex w-full cursor-pointer flex-col gap-0.5 rounded-lg border border-slate-200 px-4 py-3 " +
  "text-left transition-colors hover:border-brand-300 hover:bg-brand-50/50 " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500";

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
      <p className="text-sm text-slate-600">Cosa vuoi aggiungere?</p>
      <div className="mt-3 space-y-2">
        <button type="button" className={choiceClasses} onClick={() => date && onEvento(date)}>
          <span className="inline-flex items-center gap-2 text-sm font-medium text-slate-900">
            <CalendarPlus className="size-4 text-brand-500" aria-hidden />
            Evento personale
          </span>
          <span className="text-xs text-slate-500">
            Un promemoria sul tuo calendario, visibile solo a te.
          </span>
        </button>
        <button type="button" className={choiceClasses} onClick={() => date && onSlot(date)}>
          <span className="inline-flex items-center gap-2 text-sm font-medium text-slate-900">
            <CalendarClock className="size-4 text-emerald-600" aria-hidden />
            Slot di disponibilità
          </span>
          <span className="text-xs text-slate-500">
            Prenotabile dai clienti delle consulenze che ti vengono assegnate.
          </span>
        </button>
      </div>
    </Dialog>
  );
}
