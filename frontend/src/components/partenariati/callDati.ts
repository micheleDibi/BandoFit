import { CALL_COPY } from "../../lib/copy";
import { nomePaese } from "../../lib/paesi";
import type {
  BudgetFasciaCall,
  CallVistaCreatore,
  CriterioPartner,
  PosizioneCall,
  PosizioneInput,
  PosizioneProposta,
  RequisitoCall,
  RequisitoInput,
  RilievoCall,
  StatoCall,
} from "../../types";
import { VARIABILE } from "./PartenariatoRegole";

/* Dati e regole della call condivisi tra wizard e pagina della call: limiti
 * dello schema del server (backend/app/schemas/partner_call.py), conversioni
 * da risposta a corpo delle richieste, importi e percentuali, descrizione dei
 * criteri. Nessuna chiamata: funzioni pure. */

// ---- Limiti (gli stessi dello schema del server) ------------------------------

export const LIMITI_CALL = {
  titoloMin: 10,
  titoloMax: 140,
  descrizioneMax: 3000,
  riservatiMax: 5000,
  profiloMax: 2000,
  overrideMin: 20,
  overrideMax: 1000,
  testoRequisitoMin: 3,
  testoRequisitoMax: 500,
  titoloPosizioneMin: 3,
  titoloPosizioneMax: 120,
  notePosizioneMax: 500,
  tipiPosizioneMax: 5,
  competenzePosizioneMax: 10,
  atecoPosizioneMax: 10,
  regioniMax: 21,
  paesiMax: 30,
  requisitiPosizioneMax: 20,
  numeroPosizioneMax: 10,
  requisitiMax: 40,
  posizioniMax: 10,
  descrizioneSegnalazioneMin: 10,
  descrizioneSegnalazioneMax: 2000,
} as const;

export const NUMERO_PASSI = 7;

/** Passo del wizard dai searchParams: fuori intervallo o assente → fallback. */
export function passoDa(valore: string | null, fallback: number): number {
  const n = Number(valore);
  if (Number.isInteger(n) && n >= 1 && n <= NUMERO_PASSI) return n;
  return Math.min(Math.max(1, Math.trunc(fallback) || 1), NUMERO_PASSI);
}

/** Stati in cui la call «occupa» il bando (una sola per azienda × bando). */
const STATI_APERTI: readonly StatoCall[] = ["bozza", "pubblicata", "sospesa_moderazione"];
export const callAperta = (stato: StatoCall) => STATI_APERTI.includes(stato);

/** Il wizard modifica solo bozze e call pubblicate (queste su una whitelist). */
export const callModificabile = (call: CallVistaCreatore) =>
  call.editable && (call.stato === "bozza" || call.stato === "pubblicata");

/** Dove porta una call della lista: le bozze riprendono dal loro passo. */
export function linkCall(call: { id: string; stato: StatoCall; wizard_passo?: number | null }) {
  if (call.stato === "bozza") {
    return `/app/partenariati/call/${call.id}/modifica?passo=${passoDa(null, call.wizard_passo ?? 1)}`;
  }
  return `/app/partenariati/call/${call.id}?tab=panoramica`;
}

/** Stato di un bando in cui si può creare o pubblicare una call. */
export function bandoAperto(stato: string | null | undefined): boolean {
  const s = (stato ?? "").trim().toLowerCase();
  return s === "aperto" || s === "in apertura prossimamente";
}

// ---- Importi e percentuali (decimali come stringhe verso il server) ----------

export type Lettura = { ok: true; valore: string | null } | { ok: false; errore: string };

/** «1.200.000», «1200000,50», «1 200 000 €» → "1200000.50". Vuoto → null. */
export function leggiImporto(testo: string): Lettura {
  let t = testo.replace(/[€\s]/g, "");
  if (!t) return { ok: true, valore: null };
  if (t.includes(",")) t = t.replace(/\./g, "").replace(",", ".");
  else if (/^\d{1,3}(\.\d{3})+$/.test(t)) t = t.replace(/\./g, "");
  if (!/^\d+(\.\d{1,2})?$/.test(t)) {
    return { ok: false, errore: "Scrivi un importo in euro, con al massimo due decimali" };
  }
  if (Number(t) <= 0) return { ok: false, errore: "L'importo deve essere maggiore di zero" };
  if (t.split(".")[0].replace(/^0+/, "").length > 12) {
    return { ok: false, errore: "L'importo è troppo grande" };
  }
  return { ok: true, valore: t };
}

/** «30», «12,5», «12.5 %» → "12.5". Vuoto → null. Deve stare in (0, 100]. */
export function leggiPercentuale(testo: string): Lettura {
  const t = testo.replace(/[%\s]/g, "").replace(",", ".");
  if (!t) return { ok: true, valore: null };
  if (!/^\d{1,3}(\.\d{1,2})?$/.test(t)) {
    return { ok: false, errore: "Scrivi una percentuale, con al massimo due decimali" };
  }
  const n = Number(t);
  if (n <= 0 || n > 100) {
    return { ok: false, errore: "La percentuale deve essere maggiore di 0 e al massimo 100" };
  }
  return { ok: true, valore: t };
}

const numeroIt = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 2 });

/** Decimale del server ("30.50") in italiano ("30,5"); vuoto se assente. */
export function mostraDecimale(valore: string | number | null | undefined): string {
  if (valore === null || valore === undefined || valore === "") return "";
  const n = typeof valore === "number" ? valore : Number(valore);
  return Number.isFinite(n) ? numeroIt.format(n) : String(valore);
}

export const percentuale = (valore: string | number | null | undefined) =>
  mostraDecimale(valore) ? `${mostraDecimale(valore)}%` : "—";

/** Estremi della fascia (minimo escluso, massimo incluso; null = senza tetto). */
export const ESTREMI_BUDGET: Record<BudgetFasciaCall, [number, number | null]> = {
  fino_50k: [0, 50_000],
  "50k_150k": [50_000, 150_000],
  "150k_300k": [150_000, 300_000],
  "300k_500k": [300_000, 500_000],
  "500k_1m": [500_000, 1_000_000],
  "1m_2m": [1_000_000, 2_000_000],
  "2m_5m": [2_000_000, 5_000_000],
  oltre_5m: [5_000_000, null],
};
export const FASCE_BUDGET = Object.keys(ESTREMI_BUDGET) as BudgetFasciaCall[];

export function budgetNellaFascia(fascia: BudgetFasciaCall, importo: string): boolean {
  const n = Number(importo);
  const [minimo, massimo] = ESTREMI_BUDGET[fascia];
  return Number.isFinite(n) && n > minimo && (massimo === null || n <= massimo);
}

/** La fascia in cui cade un importo (per proporla quando si scrive il budget). */
export function fasciaDi(importo: string): BudgetFasciaCall | null {
  return FASCE_BUDGET.find((f) => budgetNellaFascia(f, importo)) ?? null;
}

// ---- Da risposta a corpo della richiesta --------------------------------------

/** Il corpo di un requisito: solo i campi di `RequisitoIn` (copertura e
 *  ordine li calcola il server; lui rifiuta le chiavi in più). */
export function requisitoInput(r: RequisitoCall | RequisitoInput): RequisitoInput {
  return {
    id: r.id ?? null,
    // L'etichetta conta solo per i requisiti già salvati: ai nuovi la assegna il server.
    etichetta: r.id ? (r.etichetta ?? null) : null,
    testo: r.testo.trim(),
    criterio: r.criterio ?? null,
    ambito: r.ambito,
    cercato: r.cercato,
    origine: r.origine,
    rif_origine: r.rif_origine ?? null,
    citazione: r.citazione ?? null,
  };
}

/** Il corpo di una posizione (anche da una proposta dell'AI). */
export function posizioneInput(p: PosizioneCall | PosizioneProposta | PosizioneInput): PosizioneInput {
  return {
    id: "id" in p ? (p.id ?? null) : null,
    titolo: p.titolo.trim(),
    ruolo: p.ruolo,
    tipi_soggetto: [...p.tipi_soggetto],
    competenze: [...p.competenze],
    ateco_divisioni: [...p.ateco_divisioni],
    regioni: [...p.regioni],
    territorio_modalita: p.territorio_modalita,
    paesi: [...p.paesi],
    dimensioni: [...p.dimensioni],
    quota_ipotizzata_pct: p.quota_ipotizzata_pct ?? null,
    numero: p.numero,
    requisiti_ids: [...p.requisiti_ids],
    note: p.note?.trim() ? p.note.trim() : null,
  };
}

/** Somma delle quote (la tua + quelle delle posizioni × numero di partner),
 *  o null se non c'è nessuna quota indicata. */
export function sommaQuote(quotaCreatore: string | null, posizioni: PosizioneInput[]) {
  let totale = 0;
  let qualcuna = false;
  const tuo = quotaCreatore ? Number(quotaCreatore) : NaN;
  if (Number.isFinite(tuo)) {
    totale += tuo;
    qualcuna = true;
  }
  for (const p of posizioni) {
    const q = p.quota_ipotizzata_pct ? Number(p.quota_ipotizzata_pct) : NaN;
    if (Number.isFinite(q)) {
      totale += q * p.numero;
      qualcuna = true;
    }
  }
  return qualcuna ? Math.round(totale * 100) / 100 : null;
}

/** Firma per «ci sono modifiche?» (JSON con le chiavi in ordine). */
export function firma(valore: unknown): string {
  return JSON.stringify(valore, (_chiave, v) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => a.localeCompare(b)))
      : v,
  );
}

// ---- Criteri in parole ------------------------------------------------------------

/** Nomi per descrivere criteri e posizioni (vocabolario e lookup). */
export interface NomiCall {
  tipo: (codice: string) => string;
  competenza: (codice: string) => string;
  regione: (id: number) => string;
  settore: (id: number) => string;
  programma: (id: number) => string;
}

const elenco = (voci: string[], massimo = 6) =>
  voci.length > massimo
    ? `${voci.slice(0, massimo).join(", ")} e altri ${voci.length - massimo}`
    : voci.join(", ");

/** Il criterio in una riga («Sede in: Calabria (già oggi)»). */
export function descriviCriterio(criterio: CriterioPartner | null, nomi: NomiCall): string {
  if (!criterio) return CALL_COPY.tipiCriterio.manuale;
  switch (criterio.tipo) {
    case "tipo_soggetto":
      return `Tipo di soggetto: ${elenco(criterio.valori.map(nomi.tipo))}`;
    case "tag":
      return `${criterio.modalita === "tutti" ? "Tutte queste competenze" : "Almeno una competenza tra"}: ${elenco(criterio.tags.map(nomi.competenza))}`;
    case "regione":
      return `Sede in: ${elenco(criterio.regioni_ids.map(nomi.regione))} (${criterio.modalita === "sede_attuale" ? "già oggi" : "anche aperta entro l'erogazione"})`;
    case "paese":
      return `${criterio.escludi ? "Non da" : "Da"}: ${elenco(criterio.paesi.map(nomePaese))}`;
    case "ateco":
      return `Attività ATECO: ${elenco(criterio.divisioni)}`;
    case "settore":
      return `Settore: ${elenco(criterio.settori_ids.map(nomi.settore))}`;
    case "dimensione":
      return `Dimensione: ${elenco(criterio.valori.map((v) => CALL_COPY.dimensioni[v] ?? v))}`;
    case "certificazione":
      return `Certificazioni: ${elenco(criterio.categorie.map((c) => CALL_COPY.certificazioni[c] ?? c))}`;
    case "esperienza":
      return `Esperienza in: ${elenco(criterio.programmi_ids.map(nomi.programma))}${criterio.ruolo ? ` (come ${criterio.ruolo})` : ""}`;
    case "regola_finanziaria": {
      const r = criterio.regola;
      const nome = (v: string) => VARIABILE[v as keyof typeof VARIABILE] ?? v;
      return `Regola economica: ${r.descrizione || nome(r.numeratore)}`;
    }
    case "manuale":
      return CALL_COPY.tipiCriterio.manuale;
  }
}

// ---- Rilievi anti-contatti ----------------------------------------------------------

const PARTI_CAMPO: Record<string, string> = {
  titolo: "titolo",
  note: "note",
  etichetta: "etichetta",
  testo: "testo",
};

/** Il campo del rilievo in parole: «descrizione_pubblica» → «Descrizione»,
 *  «posizioni[1].titolo» → «Posizioni, n. 2, titolo». */
export function campoRilievo(campo: string): string {
  const parti = campo.split(/[.[\]:]+/).filter(Boolean);
  if (parti.length === 0) return campo;
  const [primo, ...resto] = parti;
  const testa = CALL_COPY.rilieviCampi[primo] ?? primo.replaceAll("_", " ");
  const coda = resto.map((p) => (/^\d+$/.test(p) ? `n. ${Number(p) + 1}` : (PARTI_CAMPO[p] ?? p)));
  return [testa, ...coda].join(", ");
}

export function descriviRilievo(r: RilievoCall): string {
  const tipo = CALL_COPY.rilieviTipi[r.tipo] ?? r.tipo.replaceAll("_", " ");
  return `${campoRilievo(r.campo)}: contiene ${tipo}`;
}
