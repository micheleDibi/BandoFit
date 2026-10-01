import { cn } from "../../lib/cn";
import { daysUntil, formatTime, toLocalIsoDate } from "../../lib/format";

/** Fascia della scadenza rispetto a oggi: guida colore e peso del tempo relativo. */
export type StatoScadenza = "passata" | "urgente" | "vicina" | "lontana";

const meseAnnoFormatter = new Intl.DateTimeFormat("it-IT", { month: "short", year: "numeric" });
const giornoFormatter = new Intl.DateTimeFormat("it-IT", {
  day: "numeric",
  month: "short",
  year: "numeric",
});

/** «31 lug 2026» dalla data di calendario (`new Date(anno, mese-1, giorno)`), come
 *  il giorno mostrato in grande. Si tagliano le prime dieci cifre: con un
 *  datetime completo `formatDate` di `lib/format` lo convertirebbe nel fuso del
 *  browser, e il mese potrebbe non coincidere con il giorno visibile. */
function formatGiorno(data: string): string {
  const [anno, mese, giorno] = data.slice(0, 10).split("-").map(Number);
  if (![anno, mese, giorno].every(Number.isFinite)) return "—";
  return giornoFormatter.format(new Date(anno, mese - 1, giorno));
}

/** Giorni interi da `oggi` alla data (negativi se passata); null se la data non è valida.
 *  Senza `oggi` vale «oggi in Italia» (`daysUntil`); con `oggi` il confronto è
 *  deterministico sulla data locale di calendario (vetrina, esempi). */
function giorniAlla(data: string, oggi?: Date): number | null {
  if (!oggi) return daysUntil(data);
  const target = Date.parse(`${data.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(target)) return null;
  const base = Date.parse(`${toLocalIsoDate(oggi)}T00:00:00Z`);
  return Math.round((target - base) / 86_400_000);
}

/** Fascia della scadenza: passata (< 0), urgente (0-7 giorni), vicina (8-30), lontana. */
export function statoScadenza(data: string | null | undefined, oggi?: Date): StatoScadenza | null {
  if (!data) return null;
  const giorni = giorniAlla(data, oggi);
  if (giorni === null) return null;
  if (giorni < 0) return "passata";
  if (giorni <= 7) return "urgente";
  if (giorni <= 30) return "vicina";
  return "lontana";
}

/** Tempo relativo in parole: «tra 5 giorni», «scade oggi», «scade domani»,
 *  «scaduto ieri», «scaduto il 31 lug 2026». Vuoto senza data valida. */
export function tempoRelativo(data: string | null | undefined, oggi?: Date): string {
  if (!data) return "";
  const giorni = giorniAlla(data, oggi);
  if (giorni === null) return "";
  if (giorni < -1) return `scaduto il ${formatGiorno(data)}`;
  if (giorni === -1) return "scaduto ieri";
  if (giorni === 0) return "scade oggi";
  if (giorni === 1) return "scade domani";
  return `tra ${giorni} giorni`;
}

const coloreRelativo: Record<StatoScadenza, string> = {
  passata: "text-ink-3",
  urgente: "font-semibold text-warm-ink",
  vicina: "font-semibold text-warning-ink",
  lontana: "text-ink-3",
};

// La tessera della data: fondo e testo per urgenza (entro 7 giorni warm, entro 30
// warning, oltre accent); passata o senza conto, neutra. Testo ≥ 5,1:1 sul fondo.
const tessera: Record<StatoScadenza | "spenta", string> = {
  urgente: "bg-warm-soft text-warm-ink ring-1 ring-warm/30 ring-inset",
  vicina: "bg-warning-soft text-warning-ink ring-1 ring-warning/40 ring-inset",
  lontana: "bg-accent-soft text-accent-hover",
  passata: "bg-neutral-soft text-neutral-ink",
  spenta: "bg-neutral-soft text-neutral-ink",
};

export interface DueProps {
  /** Data ISO `YYYY-MM-DD` (o ISO completa: conta solo il giorno). */
  data?: string | null;
  /** Ora `HH:MM[:SS]`: entra nel `dateTime` e nel testo per lo screen reader. */
  ora?: string | null;
  /** «Oggi» di riferimento; assente = oggi in Italia. */
  oggi?: Date;
  /** Falso per un bando non in corso (chiuso, sospeso, revocato: `bandoInCorso`
   *  di `components/bandi/stato`): resta solo la data, spenta, senza tempo
   *  relativo né colore d'urgenza. Una data passata dice sempre «scaduto il…». */
  conConto?: boolean;
  className?: string;
}

/** Il segno della scadenza: una tessera colorata per urgenza con il giorno grande
 *  e «mese anno» sotto, poi il tempo relativo in parole. Stessa forma in elenco,
 *  scheda, calendario e Home. Il colore non basta mai: c'è sempre la parola. */
export function Due({ data, ora, oggi, conConto = true, className }: DueProps) {
  const parti = data?.slice(0, 10).split("-").map(Number) ?? [];
  const [anno, mese, giorno] = parti;
  const valida = parti.length === 3 && parti.every((n) => Number.isFinite(n));
  const stato = valida ? statoScadenza(data, oggi) : null;

  if (!data || !valida || stato === null) {
    return (
      <span
        className={cn(
          "flex w-18 shrink-0 items-center justify-center rounded-control border border-dashed border-line-control px-1.5 py-2 text-center text-caption text-ink-3",
          className,
        )}
      >
        Scadenza da definire
      </span>
    );
  }

  const passata = stato === "passata";
  // Senza conto (bando non in corso) e con la data ancora davanti: solo la data, spenta.
  const spenta = passata || !conConto;
  const relativo = passata || conConto ? tempoRelativo(data, oggi) : "";
  const dataIso = data.slice(0, 10);
  const oraBreve = ora ? formatTime(ora) : "";

  return (
    <time
      dateTime={ora ? `${dataIso}T${ora}` : dataIso}
      className={cn("flex w-18 shrink-0 flex-col items-start gap-0.5", className)}
    >
      <span aria-hidden className="flex w-full flex-col items-start gap-1">
        <span
          className={cn(
            "flex w-full flex-col items-center gap-0.5 rounded-control px-1 pt-1.5 pb-1",
            tessera[spenta ? "spenta" : stato],
          )}
        >
          <span className="text-due-day">{giorno}</span>
          <span className="whitespace-nowrap text-caption">
            {meseAnnoFormatter.format(new Date(anno, mese - 1, giorno))}
          </span>
        </span>
        {relativo && (
          <span className={cn("whitespace-nowrap text-caption", coloreRelativo[stato])}>
            {relativo}
          </span>
        )}
      </span>
      <span className="sr-only">
        Scadenza {formatGiorno(dataIso)}
        {oraBreve ? `, ore ${oraBreve}` : ""}
        {relativo ? `, ${relativo}` : ""}
      </span>
    </time>
  );
}
