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

/** Giorni interi da oggi (fuso italiano) alla data (negativo se passata). */
export function daysUntil(iso: string | null | undefined): number | null {
  if (!iso) return null;
  // Confronto tra date di calendario, entrambe ancorate a mezzanotte UTC.
  const target = Date.parse(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(target)) return null;
  const today = Date.parse(`${romeDateFormatter.format(new Date())}T00:00:00Z`);
  return Math.round((target - today) / 86_400_000);
}
