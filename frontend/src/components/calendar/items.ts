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
      return `${formatSlotOra(item.slot.inizio)} Disponibile`;
    case "appuntamento":
      return `${formatSlotOra(item.appuntamento.inizio)} ${
        item.appuntamento.ragione_sociale ?? "Consulenza"
      }`;
  }
}

/** Il tipo dell'item in parole (legenda, elenco del giorno, screen reader):
 *  il colore del chip non basta mai da solo. */
export function itemKindLabel(item: CalendarItem): string {
  switch (item.kind) {
    case "evento":
      return item.event.tipo === "bando" ? "Scadenza del bando" : "Evento personale";
    case "slot":
      return "Disponibilità";
    case "appuntamento":
      return "Appuntamento";
  }
}

/** Ruoli di colore del calendario (docs/design-system.md, veste «Navy deciso»):
 *  personali in `accent` (fondo `accent-soft`, testo `accent-hover`); scadenze
 *  dei bandi nel corallo dell'area scadenze (`warm-soft` + `warm-ink`);
 *  disponibilità come slot vuoto (`sheet`, bordo tratteggiato `line-control`,
 *  testo `ink-2`); appuntamenti nel colore dell'area consulenze (soft + ink).
 *  I ruoli pieni hanno una barretta di 2px a sinistra nel colore base. Testo
 *  ≥ 5,2:1 sul fondo (il passaggio del mouse non lo cambia). Il verde resta della
 *  compatibilità. Stesse classi per il chip, il campione della legenda e la
 *  riga dell'elenco del giorno: la parola (legenda, tipo) c'è sempre. */
// Bordo trasparente sugli altri ruoli: stessa altezza del chip tratteggiato.
const RUOLI = {
  personale: "border border-transparent border-l-2 border-l-accent bg-accent-soft text-accent-hover",
  bando: "border border-transparent border-l-2 border-l-warm bg-warm-soft text-warm-ink",
  slot: "border border-dashed border-line-control bg-sheet text-ink-2",
  appuntamento:
    "border border-transparent border-l-2 border-l-area-consulenze bg-area-consulenze-soft text-area-consulenze-ink",
} as const;

export type RuoloCalendario = keyof typeof RUOLI;

export function itemRuolo(item: CalendarItem): RuoloCalendario {
  switch (item.kind) {
    case "evento":
      return item.event.tipo === "bando" ? "bando" : "personale";
    case "slot":
      return "slot";
    case "appuntamento":
      return "appuntamento";
  }
}

export function ruoloClasses(ruolo: RuoloCalendario): string {
  return RUOLI[ruolo];
}

// Al passaggio i ruoli pieni prendono un filetto nel colore base (il fondo resta:
// il testo non perde contrasto, anche sulle celle `desk` fuori dal mese).
const HOVER: Record<RuoloCalendario, string> = {
  personale: "hover:ring-1 hover:ring-inset hover:ring-accent",
  bando: "hover:ring-1 hover:ring-inset hover:ring-warm",
  slot: "hover:bg-desk",
  appuntamento: "hover:ring-1 hover:ring-inset hover:ring-area-consulenze",
};

/** Classi del chip desktop: colore del ruolo più il passaggio del mouse. */
export function itemChipClasses(item: CalendarItem): string {
  const ruolo = itemRuolo(item);
  return `${RUOLI[ruolo]} ${HOVER[ruolo]}`;
}

/** Pallino presentazionale (celle mobile), 8px. Scadenze e appuntamenti pieni
 *  nell'ink del ruolo: il base del corallo e quello delle consulenze sul
 *  bianco e sul `desk` stanno sotto il 3:1 dei segni grafici. Personali tenui
 *  con l'anello `accent` (la forma li distingue dai pieni anche senza colore),
 *  disponibilità vuote con l'anello `ink-3`. */
const PALLINI: Record<RuoloCalendario, string> = {
  personale: "bg-accent-soft ring-1 ring-inset ring-accent",
  bando: "bg-warm-ink",
  slot: "bg-sheet ring-1 ring-inset ring-ink-3",
  appuntamento: "bg-area-consulenze-ink",
};

export function itemDotClass(item: CalendarItem): string {
  return PALLINI[itemRuolo(item)];
}
