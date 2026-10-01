// Importi sempre con il separatore delle migliaia: l'it-IT di serie non
// raggruppa sotto 10.000 («3500 €» accanto a «3.500 euro» nei testi).
// `"always"` è di Intl.NumberFormat v3: la lib ES2021 di TypeScript conosce
// solo il booleano, e un motore che non lo conosce lo legge come `true` (il
// comportamento di prima).
const RAGGRUPPA_SEMPRE = "always" as unknown as boolean;

const eurFormatter = new Intl.NumberFormat("it-IT", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 0,
  useGrouping: RAGGRUPPA_SEMPRE,
});

const eurWithCentsFormatter = new Intl.NumberFormat("it-IT", {
  style: "currency",
  currency: "EUR",
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
  useGrouping: RAGGRUPPA_SEMPRE,
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
  useGrouping: RAGGRUPPA_SEMPRE,
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

const SOLO_DATA = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Una data senza ora (`AAAA-MM-GG`, data di calendario) diventa la mezzanotte
 *  LOCALE di quel giorno: `new Date("2026-07-31")` sarebbe la mezzanotte UTC
 *  e a ovest di UTC mostrerebbe il giorno prima. Le date con l'ora restano
 *  come prima. Null se non è una data valida. */
function leggiData(iso: string): Date | null {
  const soloData = SOLO_DATA.exec(iso);
  if (soloData) {
    const [anno, mese, giorno] = soloData.slice(1).map(Number);
    const date = new Date(anno, mese - 1, giorno);
    // «2026-02-30» non esiste: il costruttore lo sposterebbe al 2 marzo.
    return date.getFullYear() === anno && date.getMonth() === mese - 1 && date.getDate() === giorno
      ? date
      : null;
  }
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = leggiData(iso);
  return date ? dateFormatter.format(date) : "—";
}

/** Data e ora ("7 lug 2026, 14:32") — per distinguere versioni nello stesso giorno. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = leggiData(iso);
  return date ? dateTimeFormatter.format(date) : "—";
}

/** Data in formato numerico gg/mm/aaaa. */
export function formatDateNumeric(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = leggiData(iso);
  return date ? numericDateFormatter.format(date) : "—";
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
