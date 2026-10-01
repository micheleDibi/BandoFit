import { Plus } from "lucide-react";
import { itemKey, itemKindLabel, itemRuolo, ruoloClasses, type CalendarItem } from "./items";
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

/** Titolo, orario e (per gli appuntamenti) il bando della riga. */
function contenuto(item: CalendarItem): { titolo: string; orario: string; nota?: string } {
  switch (item.kind) {
    case "slot":
      return {
        titolo: "Prenotabile dai clienti",
        orario: `${formatSlotOra(item.slot.inizio)}–${formatSlotOra(item.slot.fine)}`,
      };
    case "appuntamento":
      return {
        titolo: item.appuntamento.ragione_sociale ?? "Azienda",
        orario: `${formatSlotOra(item.appuntamento.inizio)}–${formatSlotOra(item.appuntamento.fine)}`,
        nota: item.appuntamento.bando_titolo,
      };
    case "evento":
      return { titolo: item.event.titolo, orario: timeLabel(item.event) };
  }
}

/** Riga dell'elenco: il campione del colore (come nella legenda) e il tipo in
 *  parole, poi titolo, orario ed eventuale bando. */
function ItemRow({ item, onOpen }: { item: CalendarItem; onOpen: () => void }) {
  const { titolo, orario, nota } = contenuto(item);
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        "flex w-full cursor-pointer items-start gap-3 rounded-control border border-line px-3 py-2.5 text-left",
        "transition-colors hover:bg-desk",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
      )}
    >
      <span
        aria-hidden
        className={cn("mt-1 size-3 shrink-0 rounded-mark", ruoloClasses(itemRuolo(item)))}
      />
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="text-small text-ink-3">{itemKindLabel(item)}</span>
        <span className="text-title-group text-ink">{titolo}</span>
        <span className="text-small text-ink-2 tabular-nums">{orario}</span>
        {nota && <span className="text-small text-ink-2">{nota}</span>}
      </span>
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
  createLabel = "Aggiungi un evento",
  onOpenItem,
}: DayEventsDialogProps) {
  return (
    <Dialog
      open={date !== null}
      onClose={onClose}
      title={date ? formatWeekdayLong(date) : ""}
      footer={
        <>
          <Button type="button" variant="secondary" onClick={onClose}>
            Chiudi
          </Button>
          <Button type="button" onClick={onCreate}>
            <Plus className="size-4" aria-hidden />
            {createLabel}
          </Button>
        </>
      }
    >
      {items.length === 0 ? (
        <p className="text-ink-3">Nessun evento in questo giorno.</p>
      ) : (
        <ul className="flex flex-col gap-2">
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
