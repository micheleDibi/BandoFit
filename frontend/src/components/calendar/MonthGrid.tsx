import { Plus } from "lucide-react";
import { cn } from "../../lib/cn";
import { formatWeekdayLong, toLocalIsoDate, weekdayShortLabels } from "../../lib/format";
import type { CalendarEvent } from "../../types";

interface MonthGridProps {
  anno: number;
  mese: number; // 1-12
  eventsByDay: Map<string, CalendarEvent[]>;
  todayIso: string;
  /** Click sul giorno (area vuota della cella). */
  onDayClick: (iso: string) => void;
  /** Click su un chip evento: apre la modifica di quell'evento. */
  onOpenEvent: (event: CalendarEvent) => void;
  /** Click su «+N altri»: elenco completo degli eventi del giorno. */
  onShowDay: (iso: string) => void;
}

const WEEKDAYS = weekdayShortLabels();
const MAX_CHIPS = 3;

/** Griglia mensile a tutta larghezza (lunedì per primo, 6 settimane fisse),
 *  compatta in verticale. Ogni cella è un div con bottoni FRATELLI (mai
 *  annidati): uno di sfondo che copre la cella (click sul giorno), i chip
 *  degli eventi e l'eventuale «+N altri» sopra. Su mobile i chip sono
 *  pallini presentazionali: il tap sul giorno apre l'elenco. */
export function MonthGrid({
  anno,
  mese,
  eventsByDay,
  todayIso,
  onDayClick,
  onOpenEvent,
  onShowDay,
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
          const col = index % 7;
          const lastRow = index >= 35;

          return (
            <div
              key={iso}
              className={cn(
                "group relative flex min-h-20 flex-col gap-1 p-1.5 sm:min-h-24 sm:p-2",
                "border-slate-100",
                !lastRow && "border-b",
                col < 6 && "border-r",
                inMonth ? "bg-white" : "bg-slate-50/60",
              )}
            >
              {/* Bottone di sfondo: click sul giorno */}
              <button
                type="button"
                onClick={() => onDayClick(iso)}
                aria-label={`${formatWeekdayLong(iso)}${
                  events.length
                    ? ` — ${events.length} ${events.length === 1 ? "evento" : "eventi"}`
                    : " — aggiungi un evento"
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
                      <button
                        type="button"
                        onClick={() => onShowDay(iso)}
                        className="cursor-pointer rounded px-1.5 py-0.5 text-left text-[11px] font-medium text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-brand-500"
                      >
                        +{events.length - MAX_CHIPS} altri
                      </button>
                    )}
                  </div>
                  {/* mobile: pallini presentazionali (il tap sul giorno apre l'elenco) */}
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
