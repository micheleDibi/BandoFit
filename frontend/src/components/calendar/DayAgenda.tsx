import { CalendarClock, Plus } from "lucide-react";
import { cn } from "../../lib/cn";
import { formatTime, formatWeekdayLong } from "../../lib/format";
import type { CalendarEvent } from "../../types";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";

interface DayAgendaProps {
  dayIso: string;
  events: CalendarEvent[];
  onCreate: () => void;
  onOpenEvent: (event: CalendarEvent) => void;
}

function timeLabel(event: CalendarEvent): string {
  if (event.tutto_il_giorno) return "Tutto il giorno";
  const start = formatTime(event.ora_inizio);
  return event.ora_fine ? `${start}–${formatTime(event.ora_fine)}` : start;
}

/** Agenda del giorno selezionato: è QUI che si interagisce con gli eventi
 *  (i chip nella griglia sono solo presentazionali). */
export function DayAgenda({ dayIso, events, onCreate, onOpenEvent }: DayAgendaProps) {
  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-display text-base font-semibold capitalize text-slate-900">
          {formatWeekdayLong(dayIso)}
        </h2>
        <Button variant="secondary" size="sm" onClick={onCreate}>
          <Plus className="size-4" aria-hidden />
          Aggiungi evento
        </Button>
      </div>

      {events.length === 0 ? (
        <p className="mt-3 text-sm text-slate-400">Nessun evento in questo giorno.</p>
      ) : (
        <ul className="mt-3 space-y-1.5">
          {events.map((event) => (
            <li key={event.id}>
              <button
                type="button"
                onClick={() => onOpenEvent(event)}
                className={cn(
                  "flex w-full cursor-pointer flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded-lg border px-3 py-2 text-left text-sm transition-colors",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500",
                  event.tipo === "bando"
                    ? "border-amber-200 bg-amber-50 hover:bg-amber-100"
                    : "border-slate-200 bg-white hover:bg-slate-50",
                )}
              >
                <span className="tabular shrink-0 text-xs font-medium text-slate-500">
                  {timeLabel(event)}
                </span>
                <span className="min-w-0 flex-1 font-medium text-slate-800">{event.titolo}</span>
                {event.tipo === "bando" && (
                  <span className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-amber-700">
                    <CalendarClock className="size-3.5" aria-hidden />
                    Scadenza bando
                  </span>
                )}
                {event.note && (
                  <span className="w-full truncate text-xs text-slate-400">{event.note}</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
