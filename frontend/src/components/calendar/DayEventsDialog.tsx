import { CalendarClock, Plus } from "lucide-react";
import { cn } from "../../lib/cn";
import { formatTime, formatWeekdayLong } from "../../lib/format";
import type { CalendarEvent } from "../../types";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";

interface DayEventsDialogProps {
  date: string | null; // YYYY-MM-DD, null = chiuso
  events: CalendarEvent[];
  onClose: () => void;
  onCreate: () => void;
  onOpenEvent: (event: CalendarEvent) => void;
}

function timeLabel(event: CalendarEvent): string {
  if (event.tutto_il_giorno) return "Tutto il giorno";
  const start = formatTime(event.ora_inizio);
  return event.ora_fine ? `${start}–${formatTime(event.ora_fine)}` : start;
}

/** Elenco degli eventi di un giorno: si apre dal «+N altri» delle celle
 *  affollate e, su mobile, dal tap su un giorno con eventi. */
export function DayEventsDialog({
  date,
  events,
  onClose,
  onCreate,
  onOpenEvent,
}: DayEventsDialogProps) {
  return (
    <Dialog
      open={date !== null}
      onClose={onClose}
      title={date ? formatWeekdayLong(date) : ""}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Chiudi
          </Button>
          <Button onClick={onCreate}>
            <Plus className="size-4" aria-hidden />
            Aggiungi evento
          </Button>
        </>
      }
    >
      {events.length === 0 ? (
        <p className="text-sm text-slate-400">Nessun evento in questo giorno.</p>
      ) : (
        <ul className="space-y-2">
          {events.map((event) => (
            <li key={event.id}>
              <button
                type="button"
                onClick={() => onOpenEvent(event)}
                className={cn(
                  "w-full cursor-pointer rounded-lg border-l-2 px-3 py-2.5 text-left text-sm transition-colors",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500",
                  event.tipo === "bando"
                    ? "border-amber-500 bg-amber-50 hover:bg-amber-100"
                    : "border-brand-400 bg-white shadow-sm ring-1 ring-slate-200 hover:bg-slate-50",
                )}
              >
                <span className="block font-medium leading-snug text-slate-800">
                  {event.titolo}
                </span>
                <span className="tabular mt-0.5 block text-xs text-slate-500">
                  {timeLabel(event)}
                </span>
                {event.tipo === "bando" && (
                  <span className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-amber-700">
                    <CalendarClock className="size-3.5" aria-hidden />
                    Scadenza bando
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}
