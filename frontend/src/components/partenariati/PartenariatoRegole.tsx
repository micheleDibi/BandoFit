import { AlertTriangle, CheckCircle2, FileText } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { PARTENARIATO_COPY } from "../../lib/copy";
import { formatEur } from "../../lib/format";
import type {
  ComposizioneRegola,
  CostituzionePartenariato,
  FasePartenariato,
  FontePartenariato,
  Lookups,
  MomentoRegola,
  QuotaRegola,
  RegolaFinanziariaRegola,
  RegolePartenariato,
  StatoFontePartenariato,
  TipoDocumentoRichiesto,
  TipoSoggettoPartenariato,
  TipoVincoloPartenariato,
  VariabileFinanziaria,
  Vocabolario,
} from "../../types";
import { ModalitaBadge } from "./ModalitaBadge";
import { RegolaVoce } from "./RegolaVoce";

/* Parte presentazionale della sezione «Regole di partenariato»: etichette in
 * parole delle voci estratte, passi dell'analisi e documenti letti. Lo stato
 * (avvio, polling, errori) resta in PartenariatoSection. */

// ---- Etichette -------------------------------------------------------------

const FASI: FasePartenariato[] = ["documenti", "lettura", "analisi"];

const FASE_PASSO: Record<FasePartenariato, string> = {
  documenti: "Documenti",
  lettura: "Lettura",
  analisi: "Analisi",
};

const COSTITUZIONE: Record<CostituzionePartenariato, string> = {
  costituenda_ammessa: "Il raggruppamento può essere costituito anche dopo la domanda",
  costituita_richiesta: "Il raggruppamento deve essere già costituito alla domanda",
  non_indicato: "Il bando non dice quando va costituito il raggruppamento",
};

const RUOLO: Record<ComposizioneRegola["ruolo"], string | null> = {
  capofila: "come capofila",
  partner: "come partner",
  qualsiasi: null,
  affiliato: "come ente affiliato",
  partner_associato: "come partner associato",
};

const BASE_QUOTA: Record<QuotaRegola["base_calcolo"], string | null> = {
  costo_totale_progetto: "del costo totale del progetto",
  spese_ammissibili: "delle spese ammissibili",
  contributo: "del contributo",
  budget_partner: "del budget del partner",
  non_indicata: null,
};

const EFFETTO_VIOLAZIONE: Record<QuotaRegola["effetto_violazione"], string | null> = {
  inammissibilita_progetto: "Se non è rispettata, il progetto non è ammissibile.",
  esclusione_partner: "Se non è rispettata, il partner viene escluso.",
  perdita_maggiorazione: "Se non è rispettata, si perde la maggiorazione.",
  non_indicato: null,
};

const MOMENTO: Record<MomentoRegola, string | null> = {
  domanda: "Alla presentazione della domanda",
  concessione: "Alla concessione del contributo",
  prima_erogazione: "Alla prima erogazione",
  non_indicato: null,
};

const TIPO_VINCOLO: Record<TipoVincoloPartenariato, string> = {
  indipendenza: "Indipendenza tra i partner",
  esclusivita_partenariato: "Un solo partenariato per soggetto",
  paesi_distinti: "Partner di paesi diversi",
  sede_operativa_regione: "Sede operativa nella regione",
  costituzione_entro: "Costituzione entro una scadenza",
  requisito_capofila: "Requisito del capofila",
  altro: "Altro vincolo",
};

const TIPO_DOCUMENTO: Record<TipoDocumentoRichiesto, string> = {
  lettera_intenti: "Lettera d'intenti",
  nda: "Accordo di riservatezza (NDA)",
  term_sheet_mou: "Term sheet o protocollo d'intesa",
  mandato_collettivo: "Mandato collettivo con rappresentanza",
  atto_costitutivo: "Atto costitutivo",
  impegno_costituire: "Impegno a costituire il raggruppamento",
  accordo_partenariato: "Accordo di partenariato",
  consortium_agreement: "Consortium agreement",
  contratto_rete: "Contratto di rete",
  programma_rete: "Programma di rete",
  fondo_patrimoniale: "Fondo patrimoniale comune",
  organo_comune: "Nomina dell'organo comune",
  iscrizione_registro_imprese: "Iscrizione al Registro delle imprese",
  statuto: "Statuto",
  dichiarazione_sostitutiva: "Dichiarazione sostitutiva",
  dichiarazioni_affiliated_entities: "Dichiarazioni degli enti affiliati",
  lettere_associated_partners: "Lettere dei partner associati",
  altro: "Altro documento",
};

const VARIABILE: Record<VariabileFinanziaria, string> = {
  fatturato: "Fatturato",
  fatturato_medio_2: "Fatturato medio degli ultimi 2 anni",
  fatturato_medio_3: "Fatturato medio degli ultimi 3 anni",
  valore_produzione: "Valore della produzione",
  risultato_esercizio: "Utile (o perdita) d'esercizio",
  patrimonio_netto: "Patrimonio netto",
  capitale_sociale: "Capitale sociale",
  totale_attivo: "Totale attivo",
  debiti_totali: "Debiti totali",
  disponibilita_liquide: "Disponibilità liquide",
  mol: "Margine operativo lordo",
  ebit: "Risultato operativo",
  oneri_finanziari: "Oneri finanziari",
  costo_personale: "Costo del personale",
  dipendenti: "Dipendenti",
  bilanci_approvati_n: "Bilanci approvati",
  costo_quota: "Costo della tua quota di progetto",
  contributo_quota: "Contributo sulla tua quota",
  costo_progetto_totale: "Costo totale del progetto",
};

const OPERATORE: Record<RegolaFinanziariaRegola["operatore"], string> = {
  lt: "meno di",
  le: "al massimo",
  gt: "più di",
  ge: "almeno",
};

const AMBITO_FINANZIARIO: Record<RegolaFinanziariaRegola["ambito"], string> = {
  ciascun_partner: "Vale per ciascun partner",
  capofila: "Vale per il capofila",
  media_pesata_quote: "Vale sulla media dei partner, pesata sulle quote",
  partenariato_totale: "Vale per il partenariato nel suo insieme",
};

const STATO_FONTE: Record<StatoFontePartenariato, string> = {
  candidato: "Non ancora letto",
  bloccato_policy: "Link non utilizzabile",
  formato_non_supportato: "Formato non supportato: leggiamo solo i PDF",
  errore_download: "Download non riuscito",
  troppo_grande: "Documento troppo grande per la lettura automatica",
  non_pdf: "Il link non porta a un PDF",
  schema_non_https: "Indirizzo non sicuro (senza https): non scaricato",
  scaricato: "Scaricato",
  letto: "Letto",
  letto_parziale: "Letto in parte",
  non_leggibile: "Documento scansionato: non leggibile automaticamente",
  protetto: "Documento protetto: non leggibile automaticamente",
  corrotto: "Documento danneggiato: non leggibile",
  timeout: "Lettura interrotta: il documento richiedeva troppo tempo",
  escluso_tetto: "Non letto: superato il numero di documenti analizzabili",
};

const FONTE_LETTA: StatoFontePartenariato[] = ["letto", "letto_parziale", "scaricato"];

// ---- Formattazione ---------------------------------------------------------

const numeroIt = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 4 });
const percentualeIt = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 2 });

function umanizza(codice: string): string {
  const testo = codice.replaceAll("_", " ");
  return testo.charAt(0).toUpperCase() + testo.slice(1);
}

function numero(valore: string | number): string {
  const n = typeof valore === "number" ? valore : Number(valore);
  return Number.isFinite(n) ? numeroIt.format(n) : String(valore);
}

const percento = (n: number) => `${percentualeIt.format(n)}%`;

function intervallo(
  minimo: number | null,
  massimo: number | null,
  formato: (n: number) => string,
): string | null {
  if (minimo !== null && massimo !== null) {
    return minimo === massimo
      ? `esattamente ${formato(minimo)}`
      : `da ${formato(minimo)} a ${formato(massimo)}`;
  }
  if (minimo !== null) return `almeno ${formato(minimo)}`;
  if (massimo !== null) return `al massimo ${formato(massimo)}`;
  return null;
}

// ---- Etichette dal vocabolario ---------------------------------------------

function etichettaTipo(
  codice: TipoSoggettoPartenariato,
  testoLibero: string | null,
  vocabolario: Vocabolario | undefined,
): string {
  if (codice === "altro" && testoLibero) return testoLibero;
  return vocabolario?.tipi_soggetto.find((t) => t.codice === codice)?.etichetta ?? umanizza(codice);
}

function etichettaForma(
  codice: string,
  vocabolario: Vocabolario | undefined,
  delServer?: string,
): string {
  return (
    vocabolario?.forme.find((f) => f.codice === codice)?.etichetta ?? delServer ?? umanizza(codice)
  );
}

function titoloComposizione(voce: ComposizioneRegola, vocabolario: Vocabolario | undefined) {
  const tipo = etichettaTipo(voce.tipo_soggetto, voce.tipo_soggetto_testo, vocabolario);
  const quanti = intervallo(voce.minimo, voce.massimo, (n) => String(n));
  const ruolo = RUOLO[voce.ruolo] ?? null;
  return `${quanti ? `${tipo}: ${quanti}` : tipo}${ruolo ? ` (${ruolo})` : ""}`;
}

function titoloQuota(voce: QuotaRegola, vocabolario: Vocabolario | undefined): string {
  const chi =
    voce.ambito === "capofila"
      ? "Quota del capofila"
      : voce.ambito === "per_categoria"
        ? voce.categoria
          ? `Quota della categoria «${etichettaTipo(voce.categoria, null, vocabolario)}»`
          : "Quota per categoria"
        : "Quota di ciascun partner";
  const quanto = intervallo(voce.min_percentuale, voce.max_percentuale, percento);
  const base = BASE_QUOTA[voce.base_calcolo] ?? null;
  if (!quanto) return chi;
  return `${chi}: ${quanto}${base ? ` ${base}` : ""}`;
}

/** «Costo della tua quota / Fatturato medio degli ultimi 2 anni: al massimo 0,6». */
function formulaFinanziaria(regola: RegolaFinanziariaRegola): string | null {
  const nome = (v: VariabileFinanziaria) => VARIABILE[v] ?? umanizza(v);
  const sinistra = regola.denominatore
    ? `${nome(regola.numeratore)} / ${nome(regola.denominatore)}`
    : nome(regola.numeratore);
  let destra: string | null = null;
  if (regola.soglia_variabile) {
    const coefficiente = regola.soglia_coefficiente
      ? `${numero(regola.soglia_coefficiente)} × `
      : "";
    destra = `${coefficiente}${nome(regola.soglia_variabile)}`;
  } else if (regola.soglia !== null) {
    destra = regola.unita === "euro" ? formatEur(regola.soglia) : numero(regola.soglia);
  }
  const operatore = OPERATORE[regola.operatore];
  return destra && operatore ? `${sinistra}: ${operatore} ${destra}` : null;
}

function descrizioneFonte(fonte: FontePartenariato): string {
  const stato = STATO_FONTE[fonte.stato] ?? umanizza(fonte.stato);
  if (fonte.stato !== "letto" && fonte.stato !== "letto_parziale") return stato;
  const incluse = Array.isArray(fonte.pagine_incluse) ? fonte.pagine_incluse.length : 0;
  const pagine =
    incluse > 0
      ? fonte.pagine_totali
        ? `: ${incluse} ${incluse === 1 ? "pagina" : "pagine"} su ${fonte.pagine_totali}`
        : `: ${incluse} ${incluse === 1 ? "pagina" : "pagine"}`
      : "";
  return `${stato}${pagine}${fonte.troncato ? " (testo accorciato ai limiti di lettura)" : ""}`;
}

// ---- Blocchi ---------------------------------------------------------------

function Blocco({ titolo, children }: { titolo: string; children: ReactNode }) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-slate-800">{titolo}</h3>
      <div className="mt-2 space-y-2">{children}</div>
    </div>
  );
}

function Dettaglio({ etichetta, children }: { etichetta: string; children: ReactNode }) {
  return (
    <p className="text-slate-600">
      <span className="font-medium text-slate-700">{etichetta}:</span> {children}
    </p>
  );
}

export function Avanzamento({ fase }: { fase: FasePartenariato | null }) {
  const corrente = Math.max(0, FASI.indexOf(fase ?? "documenti"));
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50/50 px-4 py-4">
      <ol aria-label="Passi dell'analisi" className="flex flex-wrap gap-x-5 gap-y-1.5 text-xs">
        {FASI.map((passo, i) => {
          const fatto = i < corrente;
          const attuale = i === corrente;
          return (
            <li
              key={passo}
              aria-current={attuale ? "step" : undefined}
              className={cn(
                "inline-flex items-center gap-1.5",
                attuale && "font-semibold text-amber-800",
                fatto && "text-emerald-700",
                !fatto && !attuale && "text-slate-400",
              )}
            >
              {fatto ? (
                <CheckCircle2 className="size-3.5" aria-hidden />
              ) : (
                <span className="tabular">{i + 1}.</span>
              )}
              {FASE_PASSO[passo]}
              {fatto && <span className="sr-only">(fatto)</span>}
            </li>
          );
        })}
      </ol>
      <p className="mt-2 text-xs text-slate-500">
        Di solito servono da 1 a 3 minuti: puoi restare su questa pagina, le regole compaiono qui
        appena sono pronte.
      </p>
    </div>
  );
}

export function DocumentiAnalizzati({ fonti }: { fonti: FontePartenariato[] }) {
  return (
    <Blocco titolo="Documenti analizzati">
      {fonti.length === 0 ? (
        <p className="text-sm text-slate-500">
          Il bando non ha documenti PDF che possiamo leggere: abbiamo usato solo la sua scheda nel
          catalogo.
        </p>
      ) : (
        <>
          <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
            {fonti.map((fonte) => {
              const url = fonte.url && /^https:\/\//i.test(fonte.url) ? fonte.url : null;
              const letto = FONTE_LETTA.includes(fonte.stato);
              return (
                <li key={fonte.n} className="flex items-start gap-2.5 px-3.5 py-2.5 text-sm">
                  <FileText className="mt-0.5 size-4 shrink-0 text-slate-400" aria-hidden />
                  <div className="min-w-0 flex-1">
                    <p className="break-words">
                      {url ? (
                        <a
                          href={url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="font-medium text-brand-600 underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-brand-500"
                        >
                          {fonte.etichetta}
                          <span className="sr-only"> (si apre in una nuova scheda)</span>
                        </a>
                      ) : (
                        <span className="font-medium text-slate-800">{fonte.etichetta}</span>
                      )}
                      {fonte.dominio && (
                        <span className="text-xs text-slate-400"> · {fonte.dominio}</span>
                      )}
                    </p>
                    <p
                      className={cn(
                        "mt-0.5 text-xs",
                        letto && !fonte.troncato ? "text-slate-500" : "text-amber-700",
                      )}
                    >
                      {descrizioneFonte(fonte)}
                    </p>
                  </div>
                </li>
              );
            })}
          </ul>
          <p className="text-xs text-slate-500">Abbiamo letto anche la scheda del bando nel catalogo.</p>
        </>
      )}
    </Blocco>
  );
}

/** Le regole estratte, voce per voce. Ogni gruppo compare solo se ha voci. */
export function Regole({
  regole,
  fonti,
  vocabolario,
  lookups,
}: {
  regole: RegolePartenariato;
  fonti: FontePartenariato[];
  vocabolario: Vocabolario | undefined;
  lookups: Lookups | undefined;
}) {
  const effettiva = regole.modalita_effettiva;
  const dichiarata = regole.modalita?.valore;
  const forme = regole.forme_ammesse ?? [];
  const composizione = regole.composizione ?? [];
  const quote = regole.quote ?? [];
  const vincoli = regole.vincoli ?? [];
  const finanziarie = regole.regole_finanziarie ?? [];
  const documenti = regole.documenti_richiesti ?? [];
  const avvisi = regole.avvisi ?? [];
  const costituzione = regole.costituzione;
  const mostraCostituzione =
    !!costituzione && (costituzione.valore !== "non_indicato" || !!costituzione.citazione);
  const partnerMin = regole.partner_min?.valore ?? null;
  const partnerMax = regole.partner_max?.valore ?? null;
  const nomeRegione = (id: number) =>
    lookups?.regioni.find((r) => r.id === id)?.nome ?? String(id);

  return (
    <div className="space-y-6">
      <p className="text-xs text-slate-500">
        Apri una voce per leggere il passaggio del bando da cui viene. Le voci «da verificare» hanno
        un passaggio che non abbiamo ritrovato alla lettera, o un valore che non torna: controllale
        sul documento.
      </p>

      <Blocco titolo="Modalità di partecipazione">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <ModalitaBadge modalita={effettiva} />
          <span className="text-sm text-slate-600">
            {PARTENARIATO_COPY.modalitaSpiegazione[effettiva]}
          </span>
        </div>
        {dichiarata && dichiarata !== effettiva && dichiarata !== "non_determinabile" && (
          <p className="text-xs text-amber-700">
            Il testo sembra indicare «{PARTENARIATO_COPY.modalita[dichiarata]}», ma non siamo
            riusciti a confermarlo: controlla il passaggio qui sotto.
          </p>
        )}
        {effettiva === "non_determinabile" && (
          <p className="text-xs text-slate-500">
            Più in basso trovi i documenti che abbiamo letto e com'è andata la lettura.
          </p>
        )}
        {regole.modalita?.citazione && (
          <RegolaVoce titolo="Passaggio del bando sulla modalità" voce={regole.modalita} fonti={fonti} />
        )}
      </Blocco>

      {forme.length > 0 && (
        <Blocco titolo="Forme di aggregazione ammesse">
          {forme.map((forma, i) => (
            <RegolaVoce
              key={`${forma.forma}-${i}`}
              titolo={etichettaForma(forma.forma, vocabolario, forma.etichetta)}
              voce={forma}
              fonti={fonti}
            >
              {forma.note && <p className="whitespace-pre-line text-slate-600">{forma.note}</p>}
            </RegolaVoce>
          ))}
        </Blocco>
      )}

      {mostraCostituzione && (
        <Blocco titolo="Costituzione del raggruppamento">
          <RegolaVoce
            titolo={COSTITUZIONE[costituzione.valore] ?? umanizza(costituzione.valore)}
            voce={costituzione}
            fonti={fonti}
          />
        </Blocco>
      )}

      {(partnerMin !== null || partnerMax !== null || regole.conteggio_note) && (
        <Blocco titolo="Numero di partner">
          {partnerMin !== null && (
            <RegolaVoce
              titolo={`Almeno ${partnerMin} ${partnerMin === 1 ? "soggetto" : "soggetti"} nel partenariato`}
              voce={regole.partner_min}
              fonti={fonti}
            />
          )}
          {partnerMax !== null && (
            <RegolaVoce
              titolo={`Al massimo ${partnerMax} ${partnerMax === 1 ? "soggetto" : "soggetti"} nel partenariato`}
              voce={regole.partner_max}
              fonti={fonti}
            />
          )}
          {regole.conteggio_note && (
            <p className="whitespace-pre-line text-xs text-slate-500">
              Come si contano: {regole.conteggio_note}
            </p>
          )}
        </Blocco>
      )}

      {composizione.length > 0 && (
        <Blocco titolo="Chi deve far parte del partenariato">
          {composizione.map((voce, i) => {
            const regioni = voce.regioni_nomi?.length
              ? voce.regioni_nomi
              : (voce.regioni ?? []).map(nomeRegione);
            const paesi = voce.paesi ?? [];
            return (
              <RegolaVoce
                key={voce.id || i}
                titolo={titoloComposizione(voce, vocabolario)}
                voce={voce}
                fonti={fonti}
              >
                {regioni.length > 0 && <Dettaglio etichetta="Regioni">{regioni.join(", ")}</Dettaglio>}
                {paesi.length > 0 && <Dettaglio etichetta="Paesi">{paesi.join(", ")}</Dettaglio>}
                {voce.vincolo_territoriale && (
                  <Dettaglio etichetta="Vincolo territoriale">{voce.vincolo_territoriale}</Dettaglio>
                )}
              </RegolaVoce>
            );
          })}
        </Blocco>
      )}

      {quote.length > 0 && (
        <Blocco titolo="Quote di partecipazione">
          {quote.map((voce, i) => {
            const effetto = EFFETTO_VIOLAZIONE[voce.effetto_violazione] ?? null;
            return (
              <RegolaVoce
                key={voce.id || i}
                titolo={titoloQuota(voce, vocabolario)}
                voce={voce}
                fonti={fonti}
              >
                {effetto && <p className="text-slate-600">{effetto}</p>}
              </RegolaVoce>
            );
          })}
        </Blocco>
      )}

      {vincoli.length > 0 && (
        <Blocco titolo="Altri vincoli">
          {vincoli.map((voce, i) => {
            const quando = MOMENTO[voce.momento] ?? null;
            const tipo = TIPO_VINCOLO[voce.tipo] ?? umanizza(voce.tipo);
            return (
              <RegolaVoce key={voce.id || i} titolo={voce.descrizione || tipo} voce={voce} fonti={fonti}>
                <Dettaglio etichetta="Tipo">{tipo}</Dettaglio>
                {voce.parametro !== null && (
                  <Dettaglio etichetta="Valore indicato">{numero(voce.parametro)}</Dettaglio>
                )}
                {quando && <Dettaglio etichetta="Quando">{quando}</Dettaglio>}
              </RegolaVoce>
            );
          })}
        </Blocco>
      )}

      {finanziarie.length > 0 && (
        <Blocco titolo="Requisiti economico-finanziari">
          {finanziarie.map((voce, i) => {
            const formula = formulaFinanziaria(voce);
            return (
              <RegolaVoce
                key={voce.id || i}
                titolo={voce.descrizione || formula || "Requisito economico-finanziario"}
                voce={voce}
                fonti={fonti}
              >
                {formula && <Dettaglio etichetta="In sintesi">{formula}</Dettaglio>}
                {AMBITO_FINANZIARIO[voce.ambito] && (
                  <p className="text-slate-600">{AMBITO_FINANZIARIO[voce.ambito]}.</p>
                )}
              </RegolaVoce>
            );
          })}
        </Blocco>
      )}

      {documenti.length > 0 && (
        <Blocco titolo="Documenti richiesti al partenariato">
          {documenti.map((voce, i) => {
            const quando = MOMENTO[voce.momento] ?? null;
            return (
              <RegolaVoce
                key={voce.id || i}
                titolo={TIPO_DOCUMENTO[voce.tipo] ?? umanizza(voce.tipo)}
                voce={voce}
                fonti={fonti}
              >
                {voce.descrizione && (
                  <p className="whitespace-pre-line text-slate-600">{voce.descrizione}</p>
                )}
                {quando && <Dettaglio etichetta="Quando">{quando}</Dettaglio>}
              </RegolaVoce>
            );
          })}
        </Blocco>
      )}

      {(regole.fonti_insufficienti || avvisi.length > 0) && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-3 text-sm text-amber-800">
          <p className="inline-flex items-center gap-1.5 font-medium">
            <AlertTriangle className="size-4 shrink-0" aria-hidden />
            Da controllare sul bando
          </p>
          <ul className="mt-1.5 list-disc space-y-1 pl-5 text-xs">
            {regole.fonti_insufficienti && (
              <li>I documenti che abbiamo letto non bastano per un quadro completo.</li>
            )}
            {avvisi.map((avviso, i) => (
              <li key={i}>{avviso}</li>
            ))}
          </ul>
        </div>
      )}

      {regole.note && (
        <Blocco titolo="Note">
          <p className="whitespace-pre-line text-sm text-slate-600">{regole.note}</p>
        </Blocco>
      )}
    </div>
  );
}
