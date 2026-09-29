import type {
  CitazioneCall,
  CitazioneRegola,
  ComposizioneSnapshot,
  CostituzionePartenariato,
  DocumentoSnapshot,
  FormaAmmessaSnapshot,
  ModalitaPartenariato,
  OrigineVoceRegola,
  QuotaSnapshot,
  RegolaFinanziaria,
  RegoleCallSnapshot,
  RegolePartenariato,
  VincoloSnapshot,
  VoceRegola,
} from "../../types";
import { firma } from "./callDati";

/* Passo «Regole del bando»: dalle regole estratte (WP3) e dall'eventuale
 * snapshot già salvato si costruisce uno stato di lavoro voce per voce; lo
 * snapshot da inviare si ricava da lì. L'origine di ogni voce NON si sceglie:
 * si deduce.
 *   - voce nata dall'estrazione, verificata con citazione verificata e con i
 *     valori di allora → `confermata` (il server ricontrolla che coincida);
 *   - voce dell'estrazione cambiata, o da verificare → `modificata`
 *     (responsabilità del creatore; la citazione resta come riferimento);
 *   - voce nuova → `aggiunta`, senza citazione.
 * Le regole economiche entrano solo `confermata` (Q11). */

export interface Voce<V> {
  chiave: string;
  incluso: boolean;
  valori: V;
  /** Valori dell'estrazione (null = voce aggiunta dal creatore). */
  originale: V | null;
  citazione: CitazioneCall | null;
  /** Verificata con citazione verificata: si può confermare così com'è. */
  confermabile: boolean;
  /** Avvisi del server sulla voce estratta (perché è da verificare). */
  avvisi: string[];
}

type SenzaVoce<T> = Omit<T, "origine_voce" | "citazione">;
export type ValoriForma = SenzaVoce<FormaAmmessaSnapshot>;
export type ValoriComposizione = SenzaVoce<ComposizioneSnapshot>;
export type ValoriQuota = SenzaVoce<QuotaSnapshot>;
export type ValoriVincolo = SenzaVoce<VincoloSnapshot>;
export type ValoriDocumento = SenzaVoce<DocumentoSnapshot>;

export interface RegoleLavoro {
  modalita: Voce<{ valore: ModalitaPartenariato }>;
  forme: Voce<ValoriForma>[];
  costituzione: Voce<{ valore: CostituzionePartenariato }>;
  partner_min: Voce<{ valore: number }>;
  partner_max: Voce<{ valore: number }>;
  composizione: Voce<ValoriComposizione>[];
  quote: Voce<ValoriQuota>[];
  vincoli: Voce<ValoriVincolo>[];
  regole_finanziarie: Voce<RegolaFinanziaria>[];
  documenti_richiesti: Voce<ValoriDocumento>[];
}

export type SezioneLista =
  | "composizione"
  | "quote"
  | "vincoli"
  | "regole_finanziarie"
  | "documenti_richiesti";

// ---- Conversioni ----------------------------------------------------------------

/** Solo le chiavi di `CitazioneIn` (il server rifiuta le altre). */
export function citazioneDa(c: CitazioneRegola | CitazioneCall | null | undefined): CitazioneCall | null {
  if (!c) return null;
  return {
    sezione: c.sezione,
    testo: c.testo,
    verificata: c.verificata,
    fonte_etichetta: c.fonte_etichetta ?? null,
    url_documento: c.url_documento ?? null,
    pagina: c.pagina ?? null,
  };
}

const confermabileDa = (v: VoceRegola | undefined | null) =>
  !!v && v.stato === "verificata" && v.citazione?.verificata === true;

const valoriComposizione = (v: ValoriComposizione): ValoriComposizione => ({
  id: v.id,
  tipo_soggetto: v.tipo_soggetto,
  tipo_soggetto_testo: v.tipo_soggetto_testo ?? null,
  minimo: v.minimo ?? null,
  massimo: v.massimo ?? null,
  ruolo: v.ruolo,
  regioni: [...(v.regioni ?? [])],
  paesi: [...(v.paesi ?? [])],
  vincolo_territoriale: v.vincolo_territoriale ?? null,
});
const valoriQuota = (v: ValoriQuota): ValoriQuota => ({
  id: v.id,
  ambito: v.ambito,
  categoria: v.categoria ?? null,
  min_percentuale: v.min_percentuale ?? null,
  max_percentuale: v.max_percentuale ?? null,
  base_calcolo: v.base_calcolo,
  effetto_violazione: v.effetto_violazione,
});
const valoriVincolo = (v: ValoriVincolo): ValoriVincolo => ({
  id: v.id,
  tipo: v.tipo,
  descrizione: v.descrizione,
  parametro: v.parametro ?? null,
  momento: v.momento,
});
const valoriFinanziaria = (v: RegolaFinanziaria): RegolaFinanziaria => ({
  id: v.id,
  descrizione: v.descrizione,
  ambito: v.ambito,
  numeratore: v.numeratore,
  denominatore: v.denominatore ?? null,
  operatore: v.operatore,
  soglia: v.soglia ?? null,
  soglia_variabile: v.soglia_variabile ?? null,
  soglia_coefficiente: v.soglia_coefficiente ?? null,
  unita: v.unita,
});
const valoriDocumento = (v: ValoriDocumento): ValoriDocumento => ({
  id: v.id,
  tipo: v.tipo,
  descrizione: v.descrizione,
  momento: v.momento,
});

// ---- Stato di lavoro iniziale -------------------------------------------------------

function unisci<V>(
  estratte: Array<{ chiave: string; valori: V; voce: VoceRegola }>,
  salvate: Array<{ chiave: string; valori: V }> | null,
): Voce<V>[] {
  const perChiave = new Map((salvate ?? []).map((s) => [s.chiave, s]));
  const out: Voce<V>[] = [];
  for (const e of estratte) {
    const confermabile = confermabileDa(e.voce);
    const salvata = perChiave.get(e.chiave);
    perChiave.delete(e.chiave);
    out.push({
      chiave: e.chiave,
      // Senza snapshot entrano di default solo le voci verificate.
      incluso: salvate ? !!salvata : confermabile,
      valori: salvata ? salvata.valori : e.valori,
      originale: e.valori,
      citazione: citazioneDa(e.voce.citazione),
      confermabile,
      avvisi: e.voce.avvisi ?? [],
    });
  }
  // Voci salvate che l'estrazione (magari rinnovata) non ha più: restano come
  // aggiunte del creatore.
  for (const s of perChiave.values()) {
    out.push({
      chiave: s.chiave,
      incluso: true,
      valori: s.valori,
      originale: null,
      citazione: null,
      confermabile: false,
      avvisi: [],
    });
  }
  return out;
}

function singola<V>(
  estratta: { valori: V; voce: VoceRegola } | null,
  salvata: V | null | undefined,
  haSnapshot: boolean,
  predefinito: V,
  chiave: string,
): Voce<V> {
  const confermabile = confermabileDa(estratta?.voce);
  return {
    chiave,
    incluso: haSnapshot ? !!salvata : confermabile,
    valori: salvata ?? estratta?.valori ?? predefinito,
    originale: estratta?.valori ?? null,
    citazione: citazioneDa(estratta?.voce.citazione),
    confermabile,
    avvisi: estratta?.voce.avvisi ?? [],
  };
}

/** Stato di lavoro dalle regole estratte e (se c'è) dallo snapshot salvato. */
export function iniziali(
  estratte: RegolePartenariato | null,
  salvate: RegoleCallSnapshot | null,
): RegoleLavoro {
  const s = salvate;
  const e = estratte;

  const modalitaEstratta = e?.modalita ?? null;
  const modalita = singola<{ valore: ModalitaPartenariato }>(
    modalitaEstratta ? { valori: { valore: modalitaEstratta.valore }, voce: modalitaEstratta } : null,
    s ? { valore: s.modalita.valore } : null,
    !!s,
    { valore: "ammesso" },
    "modalita",
  );
  // La modalità c'è sempre; «non ammesso» non si può confermare (la call
  // richiede un bando che ammetta il partenariato): si parte da «ammesso».
  modalita.incluso = true;
  if (!s && modalita.valori.valore === "non_ammesso") modalita.valori = { valore: "ammesso" };

  const conteggio = (voce: RegolePartenariato["partner_min"] | undefined) =>
    voce && typeof voce.valore === "number" && voce.valore >= 1
      ? { valori: { valore: voce.valore }, voce }
      : null;
  const costituzioneEstratta =
    e?.costituzione && (e.costituzione.valore !== "non_indicato" || e.costituzione.citazione)
      ? { valori: { valore: e.costituzione.valore }, voce: e.costituzione }
      : null;

  return {
    modalita,
    forme: unisci(
      (e?.forme_ammesse ?? []).map((f) => ({
        chiave: f.forma,
        valori: { forma: f.forma, note: f.note ?? null },
        voce: f,
      })),
      s ? s.forme_ammesse.map((f) => ({ chiave: f.forma, valori: { forma: f.forma, note: f.note ?? null } })) : null,
    ),
    costituzione: singola(
      costituzioneEstratta,
      s?.costituzione ? { valore: s.costituzione.valore } : null,
      !!s,
      { valore: "costituenda_ammessa" },
      "costituzione",
    ),
    partner_min: singola(
      conteggio(e?.partner_min),
      s?.partner_min ? { valore: s.partner_min.valore } : null,
      !!s,
      { valore: 2 },
      "partner_min",
    ),
    partner_max: singola(
      conteggio(e?.partner_max),
      s?.partner_max ? { valore: s.partner_max.valore } : null,
      !!s,
      { valore: 10 },
      "partner_max",
    ),
    composizione: unisci(
      (e?.composizione ?? []).map((v) => ({ chiave: v.id, valori: valoriComposizione(v), voce: v })),
      s ? s.composizione.map((v) => ({ chiave: v.id, valori: valoriComposizione(v) })) : null,
    ),
    quote: unisci(
      (e?.quote ?? []).map((v) => ({ chiave: v.id, valori: valoriQuota(v), voce: v })),
      s ? s.quote.map((v) => ({ chiave: v.id, valori: valoriQuota(v) })) : null,
    ),
    vincoli: unisci(
      (e?.vincoli ?? []).map((v) => ({ chiave: v.id, valori: valoriVincolo(v), voce: v })),
      s ? s.vincoli.map((v) => ({ chiave: v.id, valori: valoriVincolo(v) })) : null,
    ),
    regole_finanziarie: unisci(
      (e?.regole_finanziarie ?? []).map((v) => ({ chiave: v.id, valori: valoriFinanziaria(v), voce: v })),
      s ? s.regole_finanziarie.map((v) => ({ chiave: v.id, valori: valoriFinanziaria(v) })) : null,
    ),
    documenti_richiesti: unisci(
      (e?.documenti_richiesti ?? []).map((v) => ({ chiave: v.id, valori: valoriDocumento(v), voce: v })),
      s ? s.documenti_richiesti.map((v) => ({ chiave: v.id, valori: valoriDocumento(v) })) : null,
    ),
  };
}

// ---- Origine e snapshot -----------------------------------------------------------------

export function origine<V>(v: Voce<V>): OrigineVoceRegola {
  if (v.originale === null) return "aggiunta";
  return v.confermabile && firma(v.valori) === firma(v.originale) ? "confermata" : "modificata";
}

/** Si può mettere nello snapshot? Le regole economiche solo se confermabili. */
export const includibile = <V,>(v: Voce<V>, sezione?: SezioneLista) =>
  sezione === "regole_finanziarie" ? v.confermabile : true;

function voceSnapshot<V>(v: Voce<V>) {
  const o = origine(v);
  return { origine_voce: o, citazione: o === "aggiunta" ? null : v.citazione };
}

/** Lo snapshot da inviare (`POST …/regole`): solo le voci incluse. */
export function snapshotDa(l: RegoleLavoro): RegoleCallSnapshot {
  const lista = <V extends object>(voci: Voce<V>[], sezione?: SezioneLista) =>
    voci
      .filter((v) => v.incluso && includibile(v, sezione))
      .map((v) => ({ ...voceSnapshot(v), ...v.valori }));
  return {
    versione: 1,
    // La scrive il server dalla riga dell'estrazione corrente.
    fonte: null,
    modalita: { ...voceSnapshot(l.modalita), valore: l.modalita.valori.valore },
    forme_ammesse: lista(l.forme),
    costituzione: l.costituzione.incluso
      ? { ...voceSnapshot(l.costituzione), valore: l.costituzione.valori.valore }
      : null,
    partner_min: l.partner_min.incluso
      ? { ...voceSnapshot(l.partner_min), valore: l.partner_min.valori.valore }
      : null,
    partner_max: l.partner_max.incluso
      ? { ...voceSnapshot(l.partner_max), valore: l.partner_max.valori.valore }
      : null,
    composizione: lista(l.composizione),
    quote: lista(l.quote),
    vincoli: lista(l.vincoli),
    regole_finanziarie: lista(l.regole_finanziarie, "regole_finanziarie").map((r) => ({
      ...r,
      origine_voce: "confermata" as const,
    })),
    documenti_richiesti: lista(l.documenti_richiesti),
  };
}

/** Controlli prima dell'invio (gli stessi dello schema del server). */
export function erroriLavoro(l: RegoleLavoro): string[] {
  const errori: string[] = [];
  if (l.modalita.valori.valore === "non_ammesso") {
    errori.push("Una call richiede un bando che ammetta il partenariato: correggi la modalità.");
  }
  const min = l.partner_min.incluso ? l.partner_min.valori.valore : null;
  const max = l.partner_max.incluso ? l.partner_max.valori.valore : null;
  for (const n of [min, max]) {
    if (n !== null && (!Number.isInteger(n) || n < 1 || n > 100)) {
      errori.push("Il numero di partner deve essere un intero tra 1 e 100.");
      break;
    }
  }
  if (min !== null && max !== null && min > max) {
    errori.push("Il numero minimo di partner supera il massimo.");
  }
  return errori;
}

/** Un identificativo nuovo per una voce aggiunta (unico nella sezione). */
export function nuovoId(esistenti: Array<{ chiave: string }>, prefisso: string): string {
  const usati = new Set(esistenti.map((v) => v.chiave));
  let i = 1;
  while (usati.has(`${prefisso}${i}`)) i += 1;
  return `${prefisso}${i}`;
}
