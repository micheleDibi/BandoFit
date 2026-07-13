import { CalendarClock, Plus } from "lucide-react";
import { itemKey, type CalendarItem } from "./items";
import { cn } from "../../lib/cn";
import { formatSlotOra, formatTime, formatWeekdayLong } from "../../lib/format";
import type { CalendarEvent } from "../../types";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";

interface DayEventsDialogProps {
  date: string | null; // YYYY-MM-DD, null = chiuso
  items: CalendarItem[];
  onClose: () => void;
  onCreate: () => void;
  /** Per i progettisti il bottone apre la scelta evento/slot: etichetta generica. */
  createLabel?: string;
  onOpenItem: (item: CalendarItem) => void;
}

function timeLabel(event: CalendarEvent): string {
  if (event.tutto_il_giorno) return "Tutto il giorno";
  const start = formatTime(event.ora_inizio);
  return event.ora_fine ? `${start}–${formatTime(event.ora_fine)}` : start;
}

const rowClasses =
  "w-full cursor-pointer rounded-lg border-l-2 px-3 py-2.5 text-left text-sm transition-colors " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500";

function ItemRow({ item, onOpen }: { item: CalendarItem; onOpen: () => void }) {
  if (item.kind === "slot") {
    const { slot } = item;
    return (
      <button
        type="button"
        onClick={onOpen}
        className={cn(rowClasses, "border-emerald-500 bg-emerald-50 hover:bg-emerald-100")}
      >
        <span className="block font-medium leading-snug text-slate-800">Disponibilità</span>
        <span className="tabular mt-0.5 block text-xs text-slate-500">
          {formatSlotOra(slot.inizio)}–{formatSlotOra(slot.fine)}
        </span>
      </button>
    );
  }
  if (item.kind === "appuntamento") {
    const { appuntamento } = item;
    return (
      <button
        type="button"
        onClick={onOpen}
        className={cn(rowClasses, "border-violet-500 bg-violet-50 hover:bg-violet-100")}
      >
        <span className="block font-medium leading-snug text-slate-800">
          {appuntamento.ragione_sociale ?? "Azienda"}
        </span>
        <span className="tabular mt-0.5 block text-xs text-slate-500">
          {formatSlotOra(appuntamento.inizio)}–{formatSlotOra(appuntamento.fine)}
        </span>
        <span className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-violet-700">
          <CalendarClock className="size-3.5" aria-hidden />
          {appuntamento.bando_titolo}
        </span>
      </button>
    );
  }
  const { event } = item;
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        rowClasses,
        event.tipo === "bando"
          ? "border-amber-500 bg-amber-50 hover:bg-amber-100"
          : "border-brand-400 bg-white shadow-sm ring-1 ring-slate-200 hover:bg-slate-50",
      )}
    >
      <span className="block font-medium leading-snug text-slate-800">{event.titolo}</span>
      <span className="tabular mt-0.5 block text-xs text-slate-500">{timeLabel(event)}</span>
      {event.tipo === "bando" && (
        <span className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-amber-700">
          <CalendarClock className="size-3.5" aria-hidden />
          Scadenza bando
        </span>
      )}
    </button>
  );
}

/** Elenco degli item di un giorno: si apre dal «+N altri» delle celle
 *  affollate e, su mobile, dal tap su un giorno con contenuti. */
export function DayEventsDialog({
  date,
  items,
  onClose,
  onCreate,
  createLabel = "Aggiungi evento",
  onOpenItem,
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
            {createLabel}
          </Button>
        </>
      }
    >
      {items.length === 0 ? (
        <p className="text-sm text-slate-400">Nessun evento in questo giorno.</p>
      ) : (
        <ul className="space-y-2">
          {items.map((item) => (
            <li key={itemKey(item)}>
              <ItemRow item={item} onOpen={() => onOpenItem(item)} />
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}
