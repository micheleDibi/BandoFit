import { toLocalIsoDate } from "./format";

export type Frequenza = "nessuna" | "giornaliera" | "feriale" | "settimanale" | "mensile";

export interface OccorrenzaSlot {
  inizio: string; // ISO UTC (toISOString)
  fine: string;
}

/** Tetto difensivo, allineato a MAX_OCCORRENZE_SERIE del backend e al limite
 *  dentro fn_create_slot_serie (0018): giornaliera per 12 mesi = 367. */
export const MAX_OCCORRENZE = 370;

/** «Ripeti fino al» massimo: 12 mesi dal giorno della prima occorrenza. */
export function maxFinoAl(data: string): string {
  const [anno, mese, giorno] = data.split("-").map(Number);
  return toLocalIsoDate(new Date(anno, mese - 1 + 12, giorno));
}

/**
 * Espande la regola di ricorrenza in occorrenze concrete.
 *
 * Ogni istante nasce come Date LOCALE (`new Date(y, m, d, hh, mm)`) e viene
 * serializzato con toISOString(): così «ogni settimana alle 10:00» resta alle
 * 10:00 a muro anche attraverso i cambi di ora legale (cambia l'offset UTC,
 * non l'orario mostrato) — per questo l'espansione vive nel browser, l'unico
 * a conoscere il fuso dell'utente (il backend non assume alcun fuso).
 * L'ora «inesistente» del passaggio all'ora legale viene normalizzata dal
 * motore JS a un istante vicino: accettato.
 *
 * Regole: giornaliera = ogni giorno; feriale = lun–ven; settimanale = stesso
 * giorno della settimana; mensile = stesso giorno del mese, e i mesi privi di
 * quel giorno (29/30/31) si SALTANO (il costruttore normalizzerebbe al mese
 * successivo). Le occorrenze con inizio già passato vengono escluse: il
 * backend rifiuterebbe l'intera serie con un 400.
 */
export function espandiRicorrenza(params: {
  /** YYYY-MM-DD, giorno della prima occorrenza (fuso del browser). */
  data: string;
  oraInizio: string; // HH:MM
  oraFine: string; // HH:MM, dopo oraInizio (validato dal chiamante)
  frequenza: Exclude<Frequenza, "nessuna">;
  finoAl: string; // YYYY-MM-DD, incluso
}): OccorrenzaSlot[] {
  const { data, oraInizio, oraFine, frequenza, finoAl } = params;
  const [anno, mese, giorno] = data.split("-").map(Number);
  const [hInizio, minInizio] = oraInizio.split(":").map(Number);
  const [hFine, minFine] = oraFine.split(":").map(Number);

  const out: OccorrenzaSlot[] = [];
  const adesso = Date.now();

  // Il limite sui passi è solo un paracadute contro un finoAl malformato:
  // entro i 12 mesi del form non si raggiunge mai.
  for (let passo = 0; passo < 400 && out.length < MAX_OCCORRENZE; passo++) {
    let candidato: Date;
    if (frequenza === "mensile") {
      candidato = new Date(anno, mese - 1 + passo, giorno);
      if (candidato.getDate() !== giorno) continue; // mese privo del giorno
    } else if (frequenza === "settimanale") {
      candidato = new Date(anno, mese - 1, giorno + passo * 7);
    } else {
      candidato = new Date(anno, mese - 1, giorno + passo);
      if (frequenza === "feriale") {
        const dow = candidato.getDay();
        if (dow === 0 || dow === 6) continue; // weekend
      }
    }
    if (toLocalIsoDate(candidato) > finoAl) break;

    const inizio = new Date(
      candidato.getFullYear(), candidato.getMonth(), candidato.getDate(),
      hInizio, minInizio,
    );
    const fine = new Date(
      candidato.getFullYear(), candidato.getMonth(), candidato.getDate(),
      hFine, minFine,
    );
    if (inizio.getTime() <= adesso) continue; // occorrenza già passata
    out.push({ inizio: inizio.toISOString(), fine: fine.toISOString() });
  }
  return out;
}
