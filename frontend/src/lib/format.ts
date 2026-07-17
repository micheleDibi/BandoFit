const eurFormatter = new Intl.NumberFormat("it-IT", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 0,
});

const eurWithCentsFormatter = new Intl.NumberFormat("it-IT", {
  style: "currency",
  currency: "EUR",
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

const dateFormatter = new Intl.DateTimeFormat("it-IT", {
  day: "numeric",
  month: "short",
  year: "numeric",
});

const numericDateFormatter = new Intl.DateTimeFormat("it-IT", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});

const dateTimeFormatter = new Intl.DateTimeFormat("it-IT", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

// Gli importi del checkout viaggiano in centesimi interi: qui sempre due
// decimali ("119,56 €"), come su una fattura — non è il prezzo di listino.
const eurCentsFormatter = new Intl.NumberFormat("it-IT", {
  style: "currency",
  currency: "EUR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function eurFromCents(cents: number | null | undefined): string {
  if (cents === null || cents === undefined || !Number.isFinite(cents)) return "—";
  return eurCentsFormatter.format(cents / 100);
}

export function formatEur(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const num = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(num)) return "—";
  return eurFormatter.format(num);
}

export function formatPrezzo(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const num = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(num)) return "—";
  return eurWithCentsFormatter.format(num);
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return dateFormatter.format(date);
}

/** Data e ora ("7 lug 2026, 14:32") — per distinguere versioni nello stesso giorno. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return dateTimeFormatter.format(date);
}

/** Data in formato numerico gg/mm/aaaa. */
export function formatDateNumeric(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return numericDateFormatter.format(date);
}

// "Oggi" nel fuso italiano (formato YYYY-MM-DD): le scadenze dei bandi sono
// date di calendario italiane e il backend le confronta su Europe/Rome — il
// fuso del browser darebbe badge in contrasto con l'ordinamento.
const romeDateFormatter = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Rome" });

// Slot e appuntamenti di consulenza: ISTANTI (timestamptz UTC), mostrati nel
// fuso del BROWSER — a differenza del calendario personale, che è wall-clock
// italiano per scelta dichiarata (migration 0008).
const slotDayFormatter = new Intl.DateTimeFormat("it-IT", {
  weekday: "long",
  day: "numeric",
  month: "long",
  year: "numeric",
});
const slotTimeFormatter = new Intl.DateTimeFormat("it-IT", {
  hour: "2-digit",
  minute: "2-digit",
});

export function formatSlotGiorno(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return slotDayFormatter.format(date);
}

export function formatSlotOra(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return slotTimeFormatter.format(date);
}

const monthYearFormatter = new Intl.DateTimeFormat("it-IT", {
  month: "long",
  year: "numeric",
});

const weekdayLongFormatter = new Intl.DateTimeFormat("it-IT", {
  weekday: "long",
  day: "numeric",
  month: "long",
});

/** Data locale in YYYY-MM-DD SENZA passare da toISOString (che a cavallo
 *  della mezzanotte UTC slitterebbe di un giorno). */
export function toLocalIsoDate(d: Date): string {
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}

/** Oggi nel fuso italiano, formato YYYY-MM-DD. */
export function todayItalyIso(): string {
  return romeDateFormatter.format(new Date());
}

/** "luglio 2026" per l'intestazione del calendario. */
export function formatMonthYear(anno: number, mese: number): string {
  return monthYearFormatter.format(new Date(anno, mese - 1, 1));
}

/** "lunedì 7 luglio" per l'agenda del giorno (input YYYY-MM-DD). */
export function formatWeekdayLong(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return weekdayLongFormatter.format(new Date(y, m - 1, d));
}

/** Etichette brevi dei giorni, lunedì per primo ("lun", "mar", …). */
export function weekdayShortLabels(): string[] {
  const formatter = new Intl.DateTimeFormat("it-IT", { weekday: "short" });
  // Il 1° gennaio 2024 è un lunedì: settimana campione.
  return Array.from({ length: 7 }, (_, i) => formatter.format(new Date(2024, 0, 1 + i)));
}

/** "HH:MM" da un orario "HH:MM:SS" (nessun parsing di Date). */
export function formatTime(t: string | null | undefined): string {
  return t ? t.slice(0, 5) : "";
}

/** Giorni interi da oggi (fuso italiano) alla data (negativo se passata). */
export function daysUntil(iso: string | null | undefined): number | null {
  if (!iso) return null;
  // Confronto tra date di calendario, entrambe ancorate a mezzanotte UTC.
  const target = Date.parse(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(target)) return null;
  const today = Date.parse(`${romeDateFormatter.format(new Date())}T00:00:00Z`);
  return Math.round((target - today) / 86_400_000);
}
