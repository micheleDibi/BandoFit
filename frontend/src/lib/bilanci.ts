/** Etichette e formattazione dei bilanci per esercizio.
 *
 *  Il frontend non calcola nulla: indicatori e fasce arrivano già pronti dal
 *  server (`BilanciOut`). Qui stanno solo i nomi italiani dei campi e dei
 *  codici, e i formati degli importi. */

import type {
  CampoBilancio,
  FonteBilancio,
  IndicatoreBilancio,
  TipoBilancio,
} from "../types";
import { formatEur } from "./format";

/** Segno meno tipografico (U+2212): più leggibile del trattino e letto come
 *  «meno» dagli screen reader. */
export const SEGNO_MENO = "−";

export interface VoceBilancio {
  campo: CampoBilancio;
  etichetta: string;
  unita: "euro" | "numero";
}

export interface GruppoVociBilancio {
  titolo: string;
  voci: VoceBilancio[];
}

/** Righe della tabella, raggruppate come in un bilancio: prima il conto
 *  economico, poi lo stato patrimoniale, poi il personale. */
export const GRUPPI_VOCI_BILANCIO: GruppoVociBilancio[] = [
  {
    titolo: "Conto economico",
    voci: [
      { campo: "fatturato", etichetta: "Fatturato", unita: "euro" },
      { campo: "valore_produzione", etichetta: "Valore della produzione", unita: "euro" },
      { campo: "ebitda", etichetta: "Margine operativo lordo (EBITDA)", unita: "euro" },
      { campo: "ebit", etichetta: "Risultato operativo (EBIT)", unita: "euro" },
      { campo: "oneri_finanziari", etichetta: "Oneri finanziari", unita: "euro" },
      { campo: "risultato_esercizio", etichetta: "Utile o perdita d'esercizio", unita: "euro" },
      { campo: "cash_flow", etichetta: "Flusso di cassa (cash flow)", unita: "euro" },
    ],
  },
  {
    titolo: "Stato patrimoniale",
    voci: [
      { campo: "totale_attivo", etichetta: "Totale attivo", unita: "euro" },
      { campo: "patrimonio_netto", etichetta: "Patrimonio netto", unita: "euro" },
      { campo: "capitale_sociale", etichetta: "Capitale sociale", unita: "euro" },
      { campo: "debiti_totali", etichetta: "Debiti totali", unita: "euro" },
      { campo: "disponibilita_liquide", etichetta: "Disponibilità liquide", unita: "euro" },
    ],
  },
  {
    titolo: "Personale",
    voci: [
      { campo: "dipendenti", etichetta: "Dipendenti", unita: "numero" },
      { campo: "costo_personale", etichetta: "Costo del personale", unita: "euro" },
      {
        campo: "retribuzione_media_lorda",
        etichetta: "Retribuzione media lorda",
        unita: "euro",
      },
    ],
  },
];

/** Fonti in ordine di affidabilità (la stessa precedenza del backend). La
 *  sigla è il marcatore breve accanto a ogni valore della tabella. */
export const FONTI_BILANCIO: Record<FonteBilancio, { etichetta: string; sigla: string }> = {
  xbrl: { etichetta: "Bilancio ufficiale", sigla: "U" },
  it_full: { etichetta: "Visura: ultimo bilancio", sigla: "V" },
  it_advanced: { etichetta: "Storico Registro Imprese", sigla: "S" },
};

export const ORDINE_FONTI: FonteBilancio[] = ["xbrl", "it_full", "it_advanced"];

/** `ignoto` non si mostra: non dice nulla all'utente. */
export const TIPO_BILANCIO_LABELS: Record<TipoBilancio, string | null> = {
  ordinario: "Ordinario",
  abbreviato: "Abbreviato",
  micro: "Micro impresa",
  ignoto: null,
};

/** Etichette dei codici di fascia calcolati dal server. Le fasce di fatturato
 *  sono le stesse del campo «Fascia di fatturato» dei dati aziendali. */
const FASCE_LABELS = {
  fatturato: {
    fino_100k: "Fino a 100.000 €",
    "100k_500k": "100.000 – 500.000 €",
    "500k_2m": "500.000 € – 2 mln €",
    "2m_10m": "2 – 10 mln €",
    "10m_50m": "10 – 50 mln €",
    oltre_50m: "Oltre 50 mln €",
  },
  patrimonio_netto: {
    negativo: "Negativo",
    fino_100k: "Fino a 100.000 €",
    "100k_500k": "100.000 – 500.000 €",
    "500k_2m": "500.000 € – 2 mln €",
    "2m_10m": "2 – 10 mln €",
    oltre_10m: "Oltre 10 mln €",
  },
  dipendenti: {
    "0": "Nessun dipendente",
    "1_9": "Da 1 a 9",
    "10_49": "Da 10 a 49",
    "50_249": "Da 50 a 249",
    "250_oltre": "250 o più",
  },
  trend_fatturato: {
    crescita: "In crescita",
    stabile: "Stabile",
    calo: "In calo",
  },
} satisfies Record<string, Record<string, string>>;

export type TipoFascia = keyof typeof FASCE_LABELS;

export const FASCE_TITOLI: Record<TipoFascia, string> = {
  fatturato: "Fatturato",
  patrimonio_netto: "Patrimonio netto",
  dipendenti: "Dipendenti",
  trend_fatturato: "Andamento del fatturato",
};

/** Etichetta di un codice di fascia; un codice sconosciuto (backend più nuovo
 *  del bundle) resta visibile così com'è invece di sparire. */
export function etichettaFascia(tipo: TipoFascia, codice: string | null): string | null {
  if (!codice) return null;
  return (FASCE_LABELS[tipo] as Record<string, string>)[codice] ?? codice;
}

const numeroFormatter = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 1 });
const compattoFormatter = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 1 });
const rapportoFormatter = new Intl.NumberFormat("it-IT", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Sostituisce il trattino del segno con il meno tipografico (un numero
 *  formattato non contiene altri trattini). */
function conMeno(testo: string): string {
  return testo.replace("-", SEGNO_MENO);
}

/** Valore di una voce della tabella: euro senza decimali oppure numero (i
 *  dipendenti possono essere una media con decimali). */
export function formatValoreBilancio(
  valore: number | null | undefined,
  unita: "euro" | "numero",
): string {
  if (valore === null || valore === undefined || !Number.isFinite(valore)) return "—";
  return conMeno(unita === "euro" ? formatEur(valore) : numeroFormatter.format(valore));
}

/** Importo breve per grafici e riepiloghi: «850 €», «45 mila €», «1,2 mln €»,
 *  «3,4 mld €». */
export function formatEurCompatto(valore: number | null | undefined): string {
  if (valore === null || valore === undefined || !Number.isFinite(valore)) return "—";
  const assoluto = Math.abs(valore);
  const segno = valore < 0 ? SEGNO_MENO : "";
  if (assoluto >= 1e9) return `${segno}${compattoFormatter.format(assoluto / 1e9)} mld €`;
  if (assoluto >= 1e6) return `${segno}${compattoFormatter.format(assoluto / 1e6)} mln €`;
  if (assoluto >= 1e4) return `${segno}${compattoFormatter.format(assoluto / 1e3)} mila €`;
  return conMeno(formatEur(valore));
}

/** Valore di un indicatore secondo la sua unità. `percentuale` arriva già in
 *  punti percentuali (12,5 = 12,5%). */
export function formatIndicatore(indicatore: IndicatoreBilancio): string {
  const { valore, unita } = indicatore;
  if (valore === null || !Number.isFinite(valore)) return "—";
  if (unita === "euro") return conMeno(formatEur(valore));
  if (unita === "percentuale") return `${conMeno(numeroFormatter.format(valore))}%`;
  return conMeno(rapportoFormatter.format(valore));
}

/** «2023» oppure «2019–2023» (dal primo all'ultimo anno). */
export function intervalloAnni(anni: number[]): string {
  if (anni.length === 0) return "";
  const primo = Math.min(...anni);
  const ultimo = Math.max(...anni);
  return primo === ultimo ? String(primo) : `${primo}–${ultimo}`;
}

/** Vero se la data di chiusura non è il 31 dicembre (esercizio non solare):
 *  solo allora vale la pena mostrarla accanto all'anno. */
export function chiusuraNonSolare(dataChiusura: string | null): boolean {
  return !!dataChiusura && !dataChiusura.endsWith("-12-31");
}
