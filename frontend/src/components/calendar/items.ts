import { formatSlotOra, formatTime, toLocalIsoDate } from "../../lib/format";
import type { AppuntamentoProgettista, CalendarEvent, Slot } from "../../types";

/** Item della griglia mensile. CONVENZIONE MISTA, deliberata (format.ts):
 *  - evento: wall-clock italiano (data + orari senza fuso, migration 0008);
 *  - slot / appuntamento: ISTANTI UTC mostrati nel fuso del BROWSER.
 *  Il giorno di slot e appuntamenti è quindi quello LOCALE del browser
 *  (toLocalIsoDate su new Date(inizio)). */
export type CalendarItem =
  | { kind: "evento"; event: CalendarEvent }
  | { kind: "slot"; slot: Slot }
  | { kind: "appuntamento"; appuntamento: AppuntamentoProgettista };

export function itemKey(item: CalendarItem): string {
  switch (item.kind) {
    case "evento":
      return `evento-${item.event.id}`;
    case "slot":
      return `slot-${item.slot.id}`;
    case "appuntamento":
      return `appuntamento-${item.appuntamento.id}`;
  }
}

export function itemDay(item: CalendarItem): string {
  switch (item.kind) {
    case "evento":
      return item.event.data;
    case "slot":
      return toLocalIsoDate(new Date(item.slot.inizio));
    case "appuntamento":
      return toLocalIsoDate(new Date(item.appuntamento.inizio));
  }
}

/** Chiave d'ordinamento nel giorno: "" (tutto il giorno) in testa, poi per
 *  orario VISUALIZZATO — l'interleaving tra le due convenzioni segue quello
 *  che l'utente legge, non gli istanti sottostanti. */
export function itemSortKey(item: CalendarItem): string {
  if (item.kind === "evento") {
    return item.event.tutto_il_giorno ? "" : formatTime(item.event.ora_inizio);
  }
  return formatSlotOra(item.kind === "slot" ? item.slot.inizio : item.appuntamento.inizio);
}

export function itemChipLabel(item: CalendarItem): string {
  switch (item.kind) {
    case "evento":
      return item.event.titolo;
    case "slot":
      return `${formatSlotOra(item.slot.inizio)} · Disponibile`;
    case "appuntamento":
      return `${formatSlotOra(item.appuntamento.inizio)} · ${
        item.appuntamento.ragione_sociale ?? "Consulenza"
      }`;
  }
}

/** Classi del chip desktop. Il ramo evento conserva le stringhe storiche di
 *  MonthGrid: per i non-progettisti il rendering non cambia. */
export function itemChipClasses(item: CalendarItem): string {
  switch (item.kind) {
    case "evento":
      return item.event.tipo === "bando"
        ? "border-l-2 border-amber-500 bg-amber-50 font-medium text-amber-900 hover:bg-amber-100"
        : "border-l-2 border-brand-400 bg-brand-50 text-brand-900 hover:bg-brand-100";
    case "slot":
      return "border-l-2 border-emerald-500 bg-emerald-50 text-emerald-900 hover:bg-emerald-100";
    case "appuntamento":
      return "border-l-2 border-violet-500 bg-violet-50 font-medium text-violet-900 hover:bg-violet-100";
  }
}

/** Pallino presentazionale (celle mobile). */
export function itemDotClass(item: CalendarItem): string {
  switch (item.kind) {
    case "evento":
      return item.event.tipo === "bando" ? "bg-amber-500" : "bg-brand-500";
    case "slot":
      return "bg-emerald-500";
    case "appuntamento":
      return "bg-violet-500";
  }
}
