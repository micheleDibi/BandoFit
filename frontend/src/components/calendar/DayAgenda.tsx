import { CalendarClock, CalendarPlus, Plus } from "lucide-react";
import { cn } from "../../lib/cn";
import { formatTime, formatWeekdayLong } from "../../lib/format";
import type { CalendarEvent } from "../../types";
import { Button } from "../ui/Button";

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

/** Agenda del giorno selezionato, resa come PANNELLO dentro la card del
 *  calendario (niente card flottante): riempie l'altezza della colonna. */
export function DayAgenda({ dayIso, events, onCreate, onOpenEvent }: DayAgendaProps) {
  return (
    <div className="flex h-full flex-col p-5">
      <h2 className="font-display text-base font-bold capitalize text-slate-900">
        {formatWeekdayLong(dayIso)}
      </h2>
      <p className="mt-0.5 text-xs text-slate-400">
        {events.length === 0
          ? "Nessun evento"
          : `${events.length} ${events.length === 1 ? "evento" : "eventi"}`}
      </p>

      <Button variant="secondary" size="sm" className="mt-3 w-full" onClick={onCreate}>
        <Plus className="size-4" aria-hidden />
        Aggiungi evento
      </Button>

      {events.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 py-10 text-center">
          <CalendarPlus className="size-8 text-slate-200" aria-hidden />
          <p className="max-w-44 text-xs leading-relaxed text-slate-400">
            Giornata libera: clicca su un giorno della griglia per pianificare qualcosa.
          </p>
        </div>
      ) : (
        <ul className="mt-4 space-y-2">
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
                {event.note && (
                  <span className="mt-0.5 block truncate text-xs text-slate-400">
                    {event.note}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
