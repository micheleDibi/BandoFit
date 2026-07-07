import { Plus } from "lucide-react";
import { cn } from "../../lib/cn";
import { formatWeekdayLong, toLocalIsoDate, weekdayShortLabels } from "../../lib/format";
import type { CalendarEvent } from "../../types";

interface MonthGridProps {
  anno: number;
  mese: number; // 1-12
  eventsByDay: Map<string, CalendarEvent[]>;
  todayIso: string;
  selectedDay: string;
  /** Click sul giorno (area vuota della cella): seleziona E apre il form. */
  onCreateDay: (iso: string) => void;
  /** Click su un chip evento: apre la modifica di quell'evento. */
  onOpenEvent: (event: CalendarEvent) => void;
}

const WEEKDAYS = weekdayShortLabels();
const MAX_CHIPS = 3;

/** Griglia mensile (lunedì per primo, 6 settimane fisse), pensata per vivere
 *  DENTRO la card del calendario: bordi interni sottili, numero del giorno in
 *  alto a sinistra, hint «+» al passaggio del mouse. Ogni cella è un div con
 *  bottoni FRATELLI (mai annidati): uno di sfondo che copre la cella
 *  (click = crea evento) e i chip degli eventi sopra (click = apri evento).
 *  Su mobile i chip sono pallini presentazionali: gli eventi si aprono
 *  dall'agenda. */
export function MonthGrid({
  anno,
  mese,
  eventsByDay,
  todayIso,
  selectedDay,
  onCreateDay,
  onOpenEvent,
}: MonthGridProps) {
  const first = new Date(anno, mese - 1, 1);
  const lead = (first.getDay() + 6) % 7; // lunedì = 0
  const cells = Array.from(
    { length: 42 },
    (_, i) => new Date(anno, mese - 1, 1 - lead + i),
  );

  return (
    <div>
      <div className="grid grid-cols-7 border-b border-slate-200 bg-slate-50/80" aria-hidden>
        {WEEKDAYS.map((label) => (
          <div
            key={label}
            className="px-2 py-2 text-center text-[11px] font-semibold uppercase tracking-wider text-slate-400"
          >
            {label}
          </div>
        ))}
      </div>

      <div className="grid grid-cols-7">
        {cells.map((day, index) => {
          const iso = toLocalIsoDate(day);
          const inMonth = day.getMonth() === mese - 1;
          const events = eventsByDay.get(iso) ?? [];
          const isToday = iso === todayIso;
          const isSelected = iso === selectedDay;
          const col = index % 7;
          const lastRow = index >= 35;

          return (
            <div
              key={iso}
              className={cn(
                "group relative flex min-h-24 flex-col gap-1 p-1.5 lg:min-h-28",
                "border-slate-100",
                !lastRow && "border-b",
                col < 6 && "border-r",
                inMonth ? "bg-white" : "bg-slate-50/60",
                isSelected && "bg-brand-50/70",
              )}
            >
              {/* Bottone di sfondo: seleziona il giorno e apre il form */}
              <button
                type="button"
                onClick={() => onCreateDay(iso)}
                aria-label={`Aggiungi un evento — ${formatWeekdayLong(iso)}${
                  events.length
                    ? `, ${events.length} ${events.length === 1 ? "evento presente" : "eventi presenti"}`
                    : ""
                }`}
                className={cn(
                  "absolute inset-0 cursor-pointer transition-colors hover:bg-slate-900/[0.03]",
                  "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand-500",
                )}
              />

              <div className="pointer-events-none relative z-10 flex items-start justify-between">
                <span
                  className={cn(
                    "inline-flex size-6 items-center justify-center rounded-full text-[13px]",
                    inMonth ? "font-medium text-slate-700" : "text-slate-300",
                    isToday && "bg-brand-500 font-semibold text-white",
                    !isToday && isSelected && "font-semibold text-brand-700",
                  )}
                >
                  {day.getDate()}
                </span>
                {/* Hint di creazione: appare al passaggio del mouse */}
                <Plus
                  className="mr-0.5 mt-1 size-3.5 text-brand-400 opacity-0 transition-opacity group-hover:opacity-100"
                  aria-hidden
                />
              </div>

              {events.length > 0 && (
                <>
                  {/* ≥sm: chip cliccabili (fratelli del bottone di sfondo) */}
                  <div className="relative z-10 hidden flex-col gap-1 sm:flex">
                    {events.slice(0, MAX_CHIPS).map((event) => (
                      <button
                        key={event.id}
                        type="button"
                        onClick={() => onOpenEvent(event)}
                        title={event.titolo}
                        className={cn(
                          "cursor-pointer truncate rounded px-1.5 py-0.5 text-left text-xs leading-snug transition-colors",
                          "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-brand-500",
                          event.tipo === "bando"
                            ? "border-l-2 border-amber-500 bg-amber-50 font-medium text-amber-900 hover:bg-amber-100"
                            : "border-l-2 border-brand-400 bg-brand-50 text-brand-900 hover:bg-brand-100",
                        )}
                      >
                        {event.titolo}
                      </button>
                    ))}
                    {events.length > MAX_CHIPS && (
                      <span className="pointer-events-none px-1.5 text-[11px] font-medium text-slate-400">
                        +{events.length - MAX_CHIPS} altri
                      </span>
                    )}
                  </div>
                  {/* mobile: pallini presentazionali (eventi nell'agenda) */}
                  <span className="pointer-events-none relative z-10 flex gap-1 sm:hidden">
                    {events.slice(0, 4).map((event) => (
                      <span
                        key={event.id}
                        className={cn(
                          "size-1.5 rounded-full",
                          event.tipo === "bando" ? "bg-amber-500" : "bg-brand-500",
                        )}
                      />
                    ))}
                  </span>
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
