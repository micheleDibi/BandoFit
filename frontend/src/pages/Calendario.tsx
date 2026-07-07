import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { DayAgenda } from "../components/calendar/DayAgenda";
import { EventDialog, type DialogState } from "../components/calendar/EventDialog";
import { MonthGrid } from "../components/calendar/MonthGrid";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ErrorState, Skeleton } from "../components/ui/states";
import { useCalendarEvents } from "../hooks/useCalendar";
import { apiErrorMessage } from "../lib/api";
import { formatMonthYear, todayItalyIso } from "../lib/format";
import type { CalendarEvent } from "../types";

/** "YYYY-MM" valido → {anno, mese}; altrimenti il mese di oggi (Roma). */
function parseMonthParam(raw: string | null): { anno: number; mese: number } {
  const match = raw?.match(/^(\d{4})-(\d{2})$/);
  if (match) {
    const anno = Number(match[1]);
    const mese = Number(match[2]);
    if (anno >= 2000 && anno <= 2100 && mese >= 1 && mese <= 12) return { anno, mese };
  }
  const oggi = todayItalyIso();
  return { anno: Number(oggi.slice(0, 4)), mese: Number(oggi.slice(5, 7)) };
}

function monthParam(anno: number, mese: number): string {
  return `${anno}-${String(mese).padStart(2, "0")}`;
}

export default function Calendario() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { anno, mese } = parseMonthParam(searchParams.get("m"));
  const todayIso = todayItalyIso();

  const { data: events, isPending, isError, error, refetch } = useCalendarEvents(anno, mese);

  // Giorno selezionato: oggi se siamo nel mese corrente, altrimenti il 1°.
  const monthKey = monthParam(anno, mese);
  const defaultDay = todayIso.startsWith(monthKey) ? todayIso : `${monthKey}-01`;
  const [selectedDay, setSelectedDay] = useState(defaultDay);
  const [dialog, setDialog] = useState<DialogState>(null);

  // Il mese può cambiare anche FUORI da goToMonth (back/forward del browser,
  // link con ?m=): la selezione non deve restare su un giorno di un altro mese.
  useEffect(() => {
    if (!selectedDay.startsWith(monthKey)) {
      setSelectedDay(todayIso.startsWith(monthKey) ? todayIso : `${monthKey}-01`);
    }
  }, [monthKey, selectedDay, todayIso]);

  const eventsByDay = useMemo(() => {
    const map = new Map<string, CalendarEvent[]>();
    for (const event of events ?? []) {
      const list = map.get(event.data);
      if (list) list.push(event);
      else map.set(event.data, [event]);
    }
    return map;
  }, [events]);

  const goToMonth = (nextAnno: number, nextMese: number, day?: string) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("m", monthParam(nextAnno, nextMese));
        return next;
      },
      { replace: true },
    );
    const key = monthParam(nextAnno, nextMese);
    setSelectedDay(day ?? (todayIso.startsWith(key) ? todayIso : `${key}-01`));
  };

  const shiftMonth = (delta: number) => {
    const base = new Date(anno, mese - 1 + delta, 1);
    goToMonth(base.getFullYear(), base.getMonth() + 1);
  };

  // Click sul giorno: seleziona (l'agenda si aggiorna) E apre subito il form
  // di creazione con la data precompilata. Su un giorno di un altro mese
  // prima si naviga lì.
  const handleCreateDay = (iso: string) => {
    if (!iso.startsWith(monthKey)) {
      goToMonth(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)), iso);
    } else {
      setSelectedDay(iso);
    }
    setDialog({ mode: "create", date: iso });
  };

  const handleOpenEvent = (event: CalendarEvent) => {
    setSelectedDay(event.data);
    setDialog({ mode: "edit", event });
  };

  return (
    <div>
      <h1 className="font-display text-2xl font-bold tracking-tight text-slate-900">
        Calendario
      </h1>
      <p className="mt-1 text-sm text-slate-500">
        Le tue scadenze e i tuoi impegni: clicca su un giorno per aggiungere un evento, su un
        evento per modificarlo.
      </p>

      {isPending ? (
        <Skeleton className="mt-5 h-[36rem] w-full" />
      ) : isError ? (
        <div className="mt-5">
          <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
        </div>
      ) : (
        <Card className="mt-5 overflow-hidden p-0">
          {/* Toolbar: mese + navigazione + legenda */}
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-3.5 sm:px-5">
            <div className="flex items-center gap-2">
              <h2 className="min-w-40 font-display text-lg font-bold capitalize text-slate-900">
                {formatMonthYear(anno, mese)}
              </h2>
              <button
                type="button"
                onClick={() => shiftMonth(-1)}
                aria-label="Mese precedente"
                className="inline-flex size-8 cursor-pointer items-center justify-center rounded-lg border border-slate-200 text-slate-600 transition-colors hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-brand-500"
              >
                <ChevronLeft className="size-4" aria-hidden />
              </button>
              <button
                type="button"
                onClick={() => shiftMonth(1)}
                aria-label="Mese successivo"
                className="inline-flex size-8 cursor-pointer items-center justify-center rounded-lg border border-slate-200 text-slate-600 transition-colors hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-brand-500"
              >
                <ChevronRight className="size-4" aria-hidden />
              </button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() =>
                  goToMonth(Number(todayIso.slice(0, 4)), Number(todayIso.slice(5, 7)), todayIso)
                }
              >
                Oggi
              </Button>
            </div>
            <div className="hidden items-center gap-4 text-xs text-slate-500 sm:flex">
              <span className="inline-flex items-center gap-1.5">
                <span className="size-2 rounded-full bg-brand-500" aria-hidden />
                Personali
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="size-2 rounded-full bg-amber-500" aria-hidden />
                Scadenze bandi
              </span>
            </div>
          </div>

          {/* Griglia + agenda incorporata (colonna a destra su desktop) */}
          <div className="flex flex-col xl:flex-row">
            <div className="min-w-0 flex-1">
              <MonthGrid
                anno={anno}
                mese={mese}
                eventsByDay={eventsByDay}
                todayIso={todayIso}
                selectedDay={selectedDay}
                onCreateDay={handleCreateDay}
                onOpenEvent={handleOpenEvent}
              />
            </div>
            <aside className="border-t border-slate-200 bg-slate-50/60 xl:w-80 xl:border-l xl:border-t-0">
              <DayAgenda
                dayIso={selectedDay}
                events={eventsByDay.get(selectedDay) ?? []}
                onCreate={() => setDialog({ mode: "create", date: selectedDay })}
                onOpenEvent={handleOpenEvent}
              />
            </aside>
          </div>
        </Card>
      )}

      <EventDialog state={dialog} onClose={() => setDialog(null)} />
    </div>
  );
}
