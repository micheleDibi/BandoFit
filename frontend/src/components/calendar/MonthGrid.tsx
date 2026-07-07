import { cn } from "../../lib/cn";
import { formatWeekdayLong, toLocalIsoDate, weekdayShortLabels } from "../../lib/format";
import type { CalendarEvent } from "../../types";

interface MonthGridProps {
  anno: number;
  mese: number; // 1-12
  eventsByDay: Map<string, CalendarEvent[]>;
  todayIso: string;
  selectedDay: string;
  onSelectDay: (iso: string) => void;
}

const WEEKDAYS = weekdayShortLabels();

/** Griglia mensile (lunedì per primo, 6 settimane fisse). Ogni cella è UN
 *  SOLO bottone di selezione del giorno: i chip degli eventi al suo interno
 *  sono presentazionali (niente interattivi annidati) — l'interazione con i
 *  singoli eventi vive nell'agenda del giorno sotto la griglia. */
export function MonthGrid({
  anno,
  mese,
  eventsByDay,
  todayIso,
  selectedDay,
  onSelectDay,
}: MonthGridProps) {
  const first = new Date(anno, mese - 1, 1);
  const lead = (first.getDay() + 6) % 7; // lunedì = 0
  const cells = Array.from(
    { length: 42 },
    (_, i) => new Date(anno, mese - 1, 1 - lead + i),
  );

  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-200 shadow-card">
      <div className="grid grid-cols-7 gap-px" aria-hidden>
        {WEEKDAYS.map((label) => (
          <div
            key={label}
            className="bg-white px-2 py-2 text-center text-xs font-medium uppercase tracking-wide text-slate-400"
          >
            {label}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-px">
        {cells.map((day) => {
          const iso = toLocalIsoDate(day);
          const inMonth = day.getMonth() === mese - 1;
          const events = eventsByDay.get(iso) ?? [];
          const isToday = iso === todayIso;
          const isSelected = iso === selectedDay;

          return (
            <button
              key={iso}
              type="button"
              onClick={() => onSelectDay(iso)}
              aria-pressed={isSelected}
              aria-label={
                formatWeekdayLong(iso) +
                (events.length
                  ? `, ${events.length} ${events.length === 1 ? "evento" : "eventi"}`
                  : "")
              }
              className={cn(
                "flex min-h-16 cursor-pointer flex-col items-stretch gap-1 p-1.5 text-left align-top transition-colors sm:min-h-24",
                "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand-500",
                inMonth ? "bg-white hover:bg-slate-50" : "bg-slate-50/70 hover:bg-slate-100",
                isSelected && "bg-brand-50 hover:bg-brand-50",
              )}
            >
              <span
                className={cn(
                  "inline-flex size-6 items-center justify-center self-end rounded-full text-xs font-medium",
                  inMonth ? "text-slate-700" : "text-slate-300",
                  isToday && "bg-brand-500 font-semibold text-white",
                )}
              >
                {day.getDate()}
              </span>

              {/* Chip presentazionali: barrette con titolo su schermi larghi, pallini su mobile */}
              {events.length > 0 && (
                <>
                  <span className="hidden flex-col gap-0.5 sm:flex">
                    {events.slice(0, 3).map((event) => (
                      <span
                        key={event.id}
                        className={cn(
                          "truncate rounded px-1 py-0.5 text-[11px] leading-tight",
                          event.tipo === "bando"
                            ? "bg-amber-100 font-medium text-amber-800"
                            : "bg-brand-100 text-brand-800",
                        )}
                      >
                        {event.titolo}
                      </span>
                    ))}
                    {events.length > 3 && (
                      <span className="px-1 text-[11px] text-slate-400">
                        +{events.length - 3}
                      </span>
                    )}
                  </span>
                  <span className="flex gap-0.5 sm:hidden">
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
            </button>
          );
        })}
      </div>
    </div>
  );
}
