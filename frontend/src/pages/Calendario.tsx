import { ChevronLeft, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AddItemChooser } from "../components/calendar/AddItemChooser";
import { AppuntamentoDialog } from "../components/calendar/AppuntamentoDialog";
import { DayEventsDialog } from "../components/calendar/DayEventsDialog";
import { EventDialog, type DialogState } from "../components/calendar/EventDialog";
import { itemDay, itemSortKey, type CalendarItem } from "../components/calendar/items";
import { MonthGrid } from "../components/calendar/MonthGrid";
import { SlotDialog, type SlotDialogState } from "../components/calendar/SlotDialog";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ErrorState, Skeleton } from "../components/ui/states";
import { useCalendarEvents } from "../hooks/useCalendar";
import { useMe } from "../hooks/useMe";
import { useAppuntamenti } from "../hooks/useProgettistaRichieste";
import { useSlots } from "../hooks/useSlots";
import { apiErrorMessage } from "../lib/api";
import { formatMonthYear, todayItalyIso } from "../lib/format";
import type { AppuntamentoProgettista } from "../types";

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
  const monthKey = monthParam(anno, mese);
  const todayIso = todayItalyIso();

  const { data: events, isPending, isError, error, refetch } = useCalendarEvents(anno, mese);

  // Per i progettisti il calendario mostra anche disponibilità e appuntamenti
  // (query disattivate per gli altri: zero richieste in più).
  const { data: me } = useMe();
  const isProgettista = me?.profile.role === "progettista";
  const { data: slots } = useSlots(isProgettista);
  const { data: appuntamenti } = useAppuntamenti(isProgettista);

  const [dialog, setDialog] = useState<DialogState>(null);
  const [slotDialog, setSlotDialog] = useState<SlotDialogState>(null);
  const [appuntamentoFor, setAppuntamentoFor] = useState<AppuntamentoProgettista | null>(null);
  // Giorno per cui scegliere COSA aggiungere (solo progettisti: evento o slot).
  const [chooserFor, setChooserFor] = useState<string | null>(null);
  // Giorno di cui mostrare l'ELENCO degli item (celle affollate / tap su mobile).
  const [dayListFor, setDayListFor] = useState<string | null>(null);

  // Item misti: eventi wall-clock italiano + slot/appuntamenti come istanti
  // UTC ancorati al giorno LOCALE del browser (convenzione mista deliberata,
  // vedi items.ts). Dedup: gli slot prenotati NON si renderizzano — il loro
  // booking compare già come appuntamento con gli stessi orari. Un errore
  // sulle query progettista non blocca il calendario: le chip non compaiono.
  const itemsByDay = useMemo(() => {
    const items: CalendarItem[] = (events ?? []).map((event) => ({ kind: "evento", event }));
    if (isProgettista) {
      for (const slot of slots ?? []) {
        if (!slot.prenotato) items.push({ kind: "slot", slot });
      }
      for (const appuntamento of appuntamenti ?? []) {
        items.push({ kind: "appuntamento", appuntamento });
      }
    }
    const map = new Map<string, CalendarItem[]>();
    for (const item of items) {
      const day = itemDay(item);
      const list = map.get(day);
      if (list) list.push(item);
      else map.set(day, [item]);
    }
    for (const list of map.values()) {
      list.sort((a, b) => itemSortKey(a).localeCompare(itemSortKey(b)));
    }
    return map;
  }, [events, slots, appuntamenti, isProgettista]);

  const goToMonth = (nextAnno: number, nextMese: number) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("m", monthParam(nextAnno, nextMese));
        return next;
      },
      { replace: true },
    );
  };

  const shiftMonth = (delta: number) => {
    const base = new Date(anno, mese - 1 + delta, 1);
    goToMonth(base.getFullYear(), base.getMonth() + 1);
  };

  // Click su un giorno: apre subito il form di creazione (data precompilata).
  // Su mobile i chip non ci sono: se il giorno ha eventi si apre l'elenco
  // (da cui si può comunque aggiungere). Un giorno di un altro mese naviga lì.
  // Creazione da un giorno: i progettisti scelgono prima COSA aggiungere.
  const startCreate = (iso: string) => {
    if (isProgettista) setChooserFor(iso);
    else setDialog({ mode: "create", date: iso });
  };

  const handleDayClick = (iso: string) => {
    if (!iso.startsWith(monthKey)) {
      goToMonth(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)));
    }
    const hasItems = (itemsByDay.get(iso) ?? []).length > 0;
    const isDesktop = window.matchMedia("(min-width: 640px)").matches;
    if (hasItems && !isDesktop) {
      setDayListFor(iso);
    } else {
      startCreate(iso);
    }
  };

  const handleOpenItem = (item: CalendarItem) => {
    setDayListFor(null);
    switch (item.kind) {
      case "evento":
        setDialog({ mode: "edit", event: item.event });
        break;
      case "slot":
        setSlotDialog({ mode: "edit", slot: item.slot });
        break;
      case "appuntamento":
        setAppuntamentoFor(item.appuntamento);
        break;
    }
  };

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-bold tracking-tight text-slate-900">
            Calendario
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            {isProgettista
              ? "Clicca su un giorno per aggiungere un evento o una disponibilità, su una voce per gestirla."
              : "Clicca su un giorno per aggiungere un evento, su un evento per modificarlo."}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-4 text-xs text-slate-500">
          <span className="inline-flex items-center gap-1.5">
            <span className="size-2 rounded-full bg-brand-500" aria-hidden />
            Personali
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="size-2 rounded-full bg-amber-500" aria-hidden />
            Scadenze bandi
          </span>
          {isProgettista && (
            <>
              <span className="inline-flex items-center gap-1.5">
                <span className="size-2 rounded-full bg-emerald-500" aria-hidden />
                Disponibilità
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="size-2 rounded-full bg-violet-500" aria-hidden />
                Appuntamenti
              </span>
            </>
          )}
        </div>
      </div>

      {isPending ? (
        <Skeleton className="mt-5 h-[34rem] w-full" />
      ) : isError ? (
        <div className="mt-5">
          <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
        </div>
      ) : (
        <Card className="mt-5 overflow-hidden p-0">
          {/* Toolbar: mese + navigazione */}
          <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-4 py-3 sm:px-5">
            <h2 className="min-w-44 font-display text-lg font-bold capitalize text-slate-900">
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
              onClick={() => goToMonth(Number(todayIso.slice(0, 4)), Number(todayIso.slice(5, 7)))}
            >
              Oggi
            </Button>
          </div>

          <MonthGrid
            anno={anno}
            mese={mese}
            itemsByDay={itemsByDay}
            todayIso={todayIso}
            onDayClick={handleDayClick}
            onOpenItem={handleOpenItem}
            onShowDay={setDayListFor}
          />
        </Card>
      )}

      <DayEventsDialog
        date={dayListFor}
        items={dayListFor ? (itemsByDay.get(dayListFor) ?? []) : []}
        onClose={() => setDayListFor(null)}
        createLabel={isProgettista ? "Aggiungi" : undefined}
        onCreate={() => {
          const date = dayListFor;
          setDayListFor(null);
          if (date) startCreate(date);
        }}
        onOpenItem={handleOpenItem}
      />

      <AddItemChooser
        date={chooserFor}
        onClose={() => setChooserFor(null)}
        onEvento={(date) => {
          setChooserFor(null);
          setDialog({ mode: "create", date });
        }}
        onSlot={(date) => {
          setChooserFor(null);
          setSlotDialog({ mode: "create", date });
        }}
      />

      <EventDialog state={dialog} onClose={() => setDialog(null)} />
      <SlotDialog state={slotDialog} onClose={() => setSlotDialog(null)} />
      <AppuntamentoDialog
        appuntamento={appuntamentoFor}
        onClose={() => setAppuntamentoFor(null)}
      />
    </div>
  );
}
