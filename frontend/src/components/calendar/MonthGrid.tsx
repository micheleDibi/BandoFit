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
const MAX_CHIPS = 4;

/** Griglia mensile (lunedì per primo, 6 settimane fisse). Ogni cella è un
 *  div con DUE livelli di bottoni FRATELLI (mai annidati): uno di sfondo che
 *  copre la cella (click = crea evento in quel giorno) e i chip degli eventi
 *  sopra (click = apri l'evento). Su mobile i chip diventano pallini
 *  presentazionali: gli eventi si aprono dall'agenda sotto la griglia. */
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
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-200 shadow-card">
      <div className="grid grid-cols-7 gap-px" aria-hidden>
        {WEEKDAYS.map((label) => (
          <div
            key={label}
            className="bg-white px-2 py-2.5 text-center text-xs font-semibold uppercase tracking-wide text-slate-400"
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
            <div
              key={iso}
              className={cn(
                "relative flex min-h-20 flex-col gap-1 p-1.5 sm:min-h-28 sm:p-2 xl:min-h-32",
                inMonth ? "bg-white" : "bg-slate-50/70",
                isSelected && "bg-brand-50",
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
                  "absolute inset-0 cursor-pointer transition-colors",
                  "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand-500",
                  inMonth ? "hover:bg-slate-100/60" : "hover:bg-slate-100",
                  isSelected && "hover:bg-brand-100/40",
                )}
              />

              <span
                className={cn(
                  "pointer-events-none relative z-10 inline-flex size-6 items-center justify-center self-end rounded-full text-xs font-medium sm:size-7 sm:text-sm",
                  inMonth ? "text-slate-700" : "text-slate-300",
                  isToday && "bg-brand-500 font-semibold text-white",
                )}
              >
                {day.getDate()}
              </span>

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
                          "cursor-pointer truncate rounded-md px-1.5 py-1 text-left text-xs leading-tight transition-colors",
                          "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-brand-500",
                          event.tipo === "bando"
                            ? "bg-amber-100 font-medium text-amber-800 hover:bg-amber-200"
                            : "bg-brand-100 text-brand-800 hover:bg-brand-200",
                        )}
                      >
                        {event.titolo}
                      </button>
                    ))}
                    {events.length > MAX_CHIPS && (
                      <span className="pointer-events-none px-1 text-[11px] text-slate-400">
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
