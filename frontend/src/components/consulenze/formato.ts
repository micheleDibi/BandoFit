import { formatSlotGiorno, formatSlotOra } from "../../lib/format";

/** Solo l'iniziale maiuscola: «giovedì 8 ottobre 2026» diventa «Giovedì 8 ottobre 2026». */
export function conIniziale(testo: string): string {
  return testo.charAt(0).toLocaleUpperCase("it-IT") + testo.slice(1);
}

/** Giorno e fascia oraria di un appuntamento, nel fuso del browser:
 *  «Giovedì 8 ottobre 2026, 10:00 – 10:30». */
export function orarioAppuntamento({ inizio, fine }: { inizio: string; fine: string }): string {
  return `${conIniziale(formatSlotGiorno(inizio))}, ${formatSlotOra(inizio)} – ${formatSlotOra(fine)}`;
}

/** Inizio di un appuntamento, per le righe degli elenchi:
 *  «Giovedì 8 ottobre 2026, 10:00». */
export function inizioAppuntamento(inizio: string): string {
  return `${conIniziale(formatSlotGiorno(inizio))}, ${formatSlotOra(inizio)}`;
}
