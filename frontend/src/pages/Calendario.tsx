import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { DayAgenda } from "../components/calendar/DayAgenda";
import { EventDialog, type DialogState } from "../components/calendar/EventDialog";
import { MonthGrid } from "../components/calendar/MonthGrid";
import { Button } from "../components/ui/Button";
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
      {/* Intestazione: navigazione mese + legenda */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="inline-flex items-center gap-2 font-display text-2xl font-bold tracking-tight text-slate-900">
          <CalendarDays className="size-6 text-brand-500" aria-hidden />
          Calendario
        </h1>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => shiftMonth(-1)}
            aria-label="Mese precedente"
            className="inline-flex size-9 cursor-pointer items-center justify-center rounded-lg text-slate-600 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-brand-500"
          >
            <ChevronLeft className="size-5" aria-hidden />
          </button>
          <span className="min-w-36 text-center font-display text-base font-semibold capitalize text-slate-900">
            {formatMonthYear(anno, mese)}
          </span>
          <button
            type="button"
            onClick={() => shiftMonth(1)}
            aria-label="Mese successivo"
            className="inline-flex size-9 cursor-pointer items-center justify-center rounded-lg text-slate-600 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-brand-500"
          >
            <ChevronRight className="size-5" aria-hidden />
          </button>
          <Button
            variant="ghost"
            size="sm"
            className="ml-1"
            onClick={() =>
              goToMonth(Number(todayIso.slice(0, 4)), Number(todayIso.slice(5, 7)), todayIso)
            }
          >
            Oggi
          </Button>
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500">
        <span className="inline-flex items-center gap-1.5">
          <span className="size-2 rounded-full bg-brand-500" aria-hidden />
          Eventi personali
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="size-2 rounded-full bg-amber-500" aria-hidden />
          Scadenze bandi
        </span>
        <span className="text-slate-400">
          Clicca su un giorno per aggiungere un evento, su un evento per modificarlo.
        </span>
      </div>

      {isPending ? (
        <div className="mt-5 space-y-4">
          <Skeleton className="h-96 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      ) : isError ? (
        <div className="mt-5">
          <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
        </div>
      ) : (
        // La griglia occupa tutta la larghezza; su desktop l'agenda del
        // giorno selezionato affianca come colonna fissa.
        <div className="mt-5 grid items-start gap-6 xl:grid-cols-[1fr_340px]">
          <MonthGrid
            anno={anno}
            mese={mese}
            eventsByDay={eventsByDay}
            todayIso={todayIso}
            selectedDay={selectedDay}
            onCreateDay={handleCreateDay}
            onOpenEvent={handleOpenEvent}
          />
          <div className="xl:sticky xl:top-20">
            <DayAgenda
              dayIso={selectedDay}
              events={eventsByDay.get(selectedDay) ?? []}
              onCreate={() => setDialog({ mode: "create", date: selectedDay })}
              onOpenEvent={handleOpenEvent}
            />
          </div>
        </div>
      )}

      <EventDialog state={dialog} onClose={() => setDialog(null)} />
    </div>
  );
}
