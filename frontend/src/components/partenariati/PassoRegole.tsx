import { AlertTriangle, CheckCircle2, Pencil, Plus, RotateCcw, Trash2 } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { useAggiornaCall, useConfermaRegole } from "../../hooks/useCallPartenariato";
import { useLookups } from "../../hooks/useLookups";
import { usePartenariatiVocabolario } from "../../hooks/usePartenariatiVocabolario";
import {
  analisiInCorso,
  useAvviaAnalisiPartenariato,
  usePartenariatoBando,
} from "../../hooks/usePartenariatoBando";
import { apiErrorMessage } from "../../lib/api";
import { cn } from "../../lib/cn";
import { PARTENARIATO_COPY } from "../../lib/copy";
import { formatDate } from "../../lib/format";
import type {
  CitazioneCall,
  ModalitaPartenariato,
  MomentoRegola,
  QuotaRegola,
  RuoloComposizione,
  TipoDocumentoRichiesto,
  TipoSoggettoPartenariato,
  TipoVincoloPartenariato,
} from "../../types";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { TextField } from "../ui/Field";
import { Skeleton } from "../ui/states";
import { BarraPasso } from "./CallStepper";
import { firma } from "./callDati";
import { TestoLungo } from "./CampiCall";
import {
  Avanzamento,
  COSTITUZIONE,
  etichettaForma,
  formulaFinanziaria,
  MOMENTO,
  TIPO_DOCUMENTO,
  TIPO_VINCOLO,
  titoloComposizione,
  titoloQuota,
} from "./PartenariatoRegole";
import { SceltaLookup } from "./PartnerProfileForm";
import { avanzamento, type PassoProps } from "./passoComune";
import {
  erroriLavoro,
  includibile,
  iniziali,
  nuovoId,
  origine,
  snapshotDa,
  type RegoleLavoro,
  type SezioneLista,
  type ValoriComposizione,
  type ValoriDocumento,
  type ValoriQuota,
  type ValoriVincolo,
  type Voce,
} from "./regoleCall";

// ---- Etichette delle scelte ------------------------------------------------------

const MODALITA_SCELTE: ModalitaPartenariato[] = ["obbligatorio", "ammesso", "non_determinabile"];
const RUOLI_COMPOSIZIONE: Record<RuoloComposizione, string> = {
  qualsiasi: "Qualsiasi ruolo",
  capofila: "Capofila",
  partner: "Partner",
  affiliato: "Ente affiliato",
  partner_associato: "Partner associato",
};
const AMBITI_QUOTA: Record<QuotaRegola["ambito"], string> = {
  per_partner: "Ciascun partner",
  per_categoria: "Una categoria di soggetti",
  capofila: "Il capofila",
};
const BASI_QUOTA: Record<QuotaRegola["base_calcolo"], string> = {
  costo_totale_progetto: "Costo totale del progetto",
  spese_ammissibili: "Spese ammissibili",
  contributo: "Contributo",
  budget_partner: "Budget del partner",
  non_indicata: "Non indicata",
};
const EFFETTI_QUOTA: Record<QuotaRegola["effetto_violazione"], string> = {
  inammissibilita_progetto: "Progetto non ammissibile",
  esclusione_partner: "Partner escluso",
  perdita_maggiorazione: "Si perde la maggiorazione",
  non_indicato: "Non indicato",
};
const MOMENTI: MomentoRegola[] = ["domanda", "concessione", "prima_erogazione", "non_indicato"];
const nomeMomento = (m: MomentoRegola) => MOMENTO[m] ?? "Momento non indicato";

const selectClasses =
  "h-10 w-full cursor-pointer rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-900 focus:border-brand-500 focus:outline-2 focus:outline-offset-0 focus:outline-brand-500/30 disabled:cursor-not-allowed disabled:bg-slate-50";

function Select<T extends string>({
  etichetta,
  valore,
  opzioni,
  onChange,
  disabled,
}: {
  etichetta: string;
  valore: T;
  opzioni: Array<[T, string]>;
  onChange: (v: T) => void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium text-slate-700">
        {etichetta}
      </label>
      <select
        id={id}
        value={valore}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value as T)}
        className={selectClasses}
      >
        {opzioni.map(([v, t]) => (
          <option key={v} value={v}>
            {t}
          </option>
        ))}
      </select>
    </div>
  );
}

/** Numero facoltativo da un campo di testo: vuoto → null, non valido → NaN. */
function numeroDa(testo: string, intero = false): number | null {
  const t = testo.trim().replace(",", ".");
  if (!t) return null;
  const n = Number(t);
  if (!Number.isFinite(n) || (intero && !Number.isInteger(n))) return Number.NaN;
  return n;
}
const testoDa = (n: number | null) => (n === null ? "" : String(n).replace(".", ","));

// ---- Voce: stato, citazione, azioni ------------------------------------------------

function BadgeOrigine<V>({ voce }: { voce: Voce<V> }) {
  const o = origine(voce);
  if (!voce.incluso) {
    return voce.originale !== null && !voce.confermabile ? (
      <Badge tone="amber">Da verificare</Badge>
    ) : null;
  }
  if (o === "confermata") {
    return (
      <Badge tone="emerald">
        <CheckCircle2 className="size-3" aria-hidden />
        Come nel bando
      </Badge>
    );
  }
  return <Badge tone="brand">{o === "aggiunta" ? "Aggiunta da te" : "Modificata da te"}</Badge>;
}

function Citazione({ citazione }: { citazione: CitazioneCall | null }) {
  if (!citazione) return null;
  return (
    <details className="text-xs text-slate-500">
      <summary className="cursor-pointer font-medium text-slate-600">
        Passaggio del bando{citazione.pagina ? ` (pagina ${citazione.pagina})` : ""}
      </summary>
      <p className="mt-1 whitespace-pre-line italic">«{citazione.testo}»</p>
      {!citazione.verificata && (
        <p className="mt-0.5 text-amber-700">Non ritrovato alla lettera nel documento.</p>
      )}
    </details>
  );
}

function RigaVoce<V>({
  voce,
  titolo,
  dettaglio,
  onIncluso,
  sezione,
  sola,
  onModifica,
  onRipristina,
  onRimuovi,
  nonIncludibile,
  children,
}: {
  voce: Voce<V>;
  titolo: ReactNode;
  dettaglio?: ReactNode;
  onIncluso: (v: boolean) => void;
  sezione?: SezioneLista;
  sola: boolean;
  onModifica?: () => void;
  onRipristina?: () => void;
  onRimuovi?: () => void;
  nonIncludibile?: string;
  children?: ReactNode;
}) {
  const id = useId();
  const puoIncludere = includibile(voce, sezione);
  const modificata = voce.originale !== null && origine(voce) === "modificata" && voce.incluso;
  return (
    <li className="rounded-lg border border-slate-200 bg-white px-3.5 py-3">
      <div className="flex flex-wrap items-start gap-3">
        <input
          id={id}
          type="checkbox"
          className="mt-0.5 size-4 shrink-0 cursor-pointer accent-brand-500 disabled:cursor-not-allowed"
          checked={voce.incluso && puoIncludere}
          disabled={sola || !puoIncludere}
          onChange={(e) => onIncluso(e.target.checked)}
          aria-describedby={nonIncludibile && !puoIncludere ? `${id}-nota` : undefined}
        />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor={id} className="cursor-pointer text-sm font-medium text-slate-800">
              {titolo}
            </label>
            <BadgeOrigine voce={voce} />
          </div>
          {dettaglio && <div className="text-sm text-slate-600">{dettaglio}</div>}
          {voce.avvisi.length > 0 && (
            <ul className="list-disc pl-5 text-xs text-amber-700">
              {voce.avvisi.map((a, i) => (
                <li key={i}>{a}</li>
              ))}
            </ul>
          )}
          {nonIncludibile && !puoIncludere && (
            <p id={`${id}-nota`} className="text-xs text-slate-500">
              {nonIncludibile}
            </p>
          )}
          <Citazione citazione={voce.citazione} />
        </div>
        {!sola && (
          <div className="flex shrink-0 flex-wrap gap-1">
            {onModifica && (
              <Button variant="ghost" size="sm" onClick={onModifica} aria-label={`Modifica: ${typeof titolo === "string" ? titolo : "voce"}`}>
                <Pencil className="size-4" aria-hidden />
                Modifica
              </Button>
            )}
            {modificata && onRipristina && voce.confermabile && (
              <Button variant="ghost" size="sm" onClick={onRipristina}>
                <RotateCcw className="size-4" aria-hidden />
                Come nel bando
              </Button>
            )}
            {onRimuovi && voce.originale === null && (
              <Button variant="ghost" size="sm" onClick={onRimuovi} aria-label="Rimuovi la voce">
                <Trash2 className="size-4" aria-hidden />
              </Button>
            )}
          </div>
        )}
      </div>
      {children}
    </li>
  );
}

function CorniceEditor({
  errori,
  onFatto,
  onAnnulla,
  children,
}: {
  errori: string[];
  onFatto: () => void;
  onAnnulla: () => void;
  children: ReactNode;
}) {
  return (
    <div className="mt-3 space-y-4 rounded-lg border border-brand-200 bg-brand-50/30 p-4">
      {children}
      {errori.length > 0 && (
        <ul className="list-disc rounded-lg bg-red-50 py-2 pl-8 pr-3 text-sm text-red-700" role="alert">
          {errori.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onAnnulla}>
          Annulla
        </Button>
        <Button onClick={onFatto}>Fatto</Button>
      </div>
    </div>
  );
}

// ---- Editor delle voci -----------------------------------------------------------------

function EditorComposizione({
  iniziale,
  onFatto,
  onAnnulla,
}: {
  iniziale: ValoriComposizione;
  onFatto: (v: ValoriComposizione) => void;
  onAnnulla: () => void;
}) {
  const { data: vocabolario } = usePartenariatiVocabolario();
  const { data: lookups } = useLookups();
  const [v, setV] = useState(iniziale);
  const [minimo, setMinimo] = useState(testoDa(iniziale.minimo));
  const [massimo, setMassimo] = useState(testoDa(iniziale.massimo));
  const [errori, setErrori] = useState<string[]>([]);
  const fatto = () => {
    const mi = numeroDa(minimo, true);
    const ma = numeroDa(massimo, true);
    const problemi: string[] = [];
    for (const n of [mi, ma]) {
      if (n !== null && (Number.isNaN(n) || n < 0 || n > 100)) {
        problemi.push("Minimo e massimo sono numeri interi tra 0 e 100.");
        break;
      }
    }
    if (mi !== null && ma !== null && mi > ma) problemi.push("Il minimo supera il massimo.");
    if (v.tipo_soggetto === "altro" && !v.tipo_soggetto_testo?.trim()) {
      problemi.push("Descrivi il tipo di soggetto.");
    }
    if (problemi.length) return setErrori(problemi);
    onFatto({
      ...v,
      minimo: mi,
      massimo: ma,
      tipo_soggetto_testo: v.tipo_soggetto === "altro" ? (v.tipo_soggetto_testo?.trim() ?? null) : v.tipo_soggetto_testo,
      vincolo_territoriale: v.vincolo_territoriale?.trim() ? v.vincolo_territoriale.trim() : null,
    });
  };
  return (
    <CorniceEditor errori={errori} onFatto={fatto} onAnnulla={onAnnulla}>
      <div className="grid gap-4 sm:grid-cols-2">
        <Select<TipoSoggettoPartenariato>
          etichetta="Tipo di soggetto"
          valore={v.tipo_soggetto}
          opzioni={(vocabolario?.tipi_soggetto ?? [{ codice: v.tipo_soggetto, etichetta: v.tipo_soggetto }]).map(
            (t) => [t.codice, t.etichetta],
          )}
          onChange={(t) => setV({ ...v, tipo_soggetto: t })}
        />
        <Select<RuoloComposizione>
          etichetta="Con quale ruolo"
          valore={v.ruolo}
          opzioni={Object.entries(RUOLI_COMPOSIZIONE) as Array<[RuoloComposizione, string]>}
          onChange={(r) => setV({ ...v, ruolo: r })}
        />
      </div>
      {v.tipo_soggetto === "altro" && (
        <TextField
          label="Descrivi il tipo di soggetto"
          maxLength={300}
          value={v.tipo_soggetto_testo ?? ""}
          onChange={(e) => setV({ ...v, tipo_soggetto_testo: e.target.value })}
        />
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField label="Almeno (facoltativo)" inputMode="numeric" value={minimo} onChange={(e) => setMinimo(e.target.value)} />
        <TextField label="Al massimo (facoltativo)" inputMode="numeric" value={massimo} onChange={(e) => setMassimo(e.target.value)} />
      </div>
      <SceltaLookup
        etichetta="Regioni (facoltative)"
        opzioni={lookups?.regioni}
        scelti={v.regioni}
        onChange={(r) => setV({ ...v, regioni: r })}
        massimo={21}
      />
      <TestoLungo
        etichetta="Vincolo territoriale (facoltativo)"
        valore={v.vincolo_territoriale ?? ""}
        onChange={(t) => setV({ ...v, vincolo_territoriale: t })}
        massimo={500}
        righe={2}
      />
    </CorniceEditor>
  );
}

function EditorQuota({
  iniziale,
  onFatto,
  onAnnulla,
}: {
  iniziale: ValoriQuota;
  onFatto: (v: ValoriQuota) => void;
  onAnnulla: () => void;
}) {
  const { data: vocabolario } = usePartenariatiVocabolario();
  const [v, setV] = useState(iniziale);
  const [minimo, setMinimo] = useState(testoDa(iniziale.min_percentuale));
  const [massimo, setMassimo] = useState(testoDa(iniziale.max_percentuale));
  const [errori, setErrori] = useState<string[]>([]);
  const fatto = () => {
    const mi = numeroDa(minimo);
    const ma = numeroDa(massimo);
    const problemi: string[] = [];
    for (const n of [mi, ma]) {
      if (n !== null && (Number.isNaN(n) || n < 0 || n > 100)) {
        problemi.push("Le percentuali sono numeri tra 0 e 100.");
        break;
      }
    }
    if (mi === null && ma === null) problemi.push("Indica almeno una percentuale.");
    if (mi !== null && ma !== null && mi > ma) problemi.push("La percentuale minima supera la massima.");
    if (v.ambito === "per_categoria" && !v.categoria) problemi.push("Scegli la categoria.");
    if (problemi.length) return setErrori(problemi);
    onFatto({
      ...v,
      min_percentuale: mi,
      max_percentuale: ma,
      categoria: v.ambito === "per_categoria" ? v.categoria : null,
    });
  };
  return (
    <CorniceEditor errori={errori} onFatto={fatto} onAnnulla={onAnnulla}>
      <div className="grid gap-4 sm:grid-cols-2">
        <Select<QuotaRegola["ambito"]>
          etichetta="A chi si applica"
          valore={v.ambito}
          opzioni={Object.entries(AMBITI_QUOTA) as Array<[QuotaRegola["ambito"], string]>}
          onChange={(a) => setV({ ...v, ambito: a })}
        />
        {v.ambito === "per_categoria" && (
          <Select<TipoSoggettoPartenariato | "">
            etichetta="Categoria"
            valore={v.categoria ?? ""}
            opzioni={[
              ["", "Scegli…"],
              ...(vocabolario?.tipi_soggetto ?? []).map(
                (t) => [t.codice, t.etichetta] as [TipoSoggettoPartenariato, string],
              ),
            ]}
            onChange={(c) => setV({ ...v, categoria: c || null })}
          />
        )}
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField label="Almeno (%)" inputMode="decimal" value={minimo} onChange={(e) => setMinimo(e.target.value)} />
        <TextField label="Al massimo (%)" inputMode="decimal" value={massimo} onChange={(e) => setMassimo(e.target.value)} />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Select<QuotaRegola["base_calcolo"]>
          etichetta="Calcolata su"
          valore={v.base_calcolo}
          opzioni={Object.entries(BASI_QUOTA) as Array<[QuotaRegola["base_calcolo"], string]>}
          onChange={(b) => setV({ ...v, base_calcolo: b })}
        />
        <Select<QuotaRegola["effetto_violazione"]>
          etichetta="Se non è rispettata"
          valore={v.effetto_violazione}
          opzioni={Object.entries(EFFETTI_QUOTA) as Array<[QuotaRegola["effetto_violazione"], string]>}
          onChange={(e) => setV({ ...v, effetto_violazione: e })}
        />
      </div>
    </CorniceEditor>
  );
}

function EditorVincolo({
  iniziale,
  onFatto,
  onAnnulla,
}: {
  iniziale: ValoriVincolo;
  onFatto: (v: ValoriVincolo) => void;
  onAnnulla: () => void;
}) {
  const [v, setV] = useState(iniziale);
  const [parametro, setParametro] = useState(testoDa(iniziale.parametro));
  const [errori, setErrori] = useState<string[]>([]);
  const fatto = () => {
    const p = numeroDa(parametro);
    const problemi: string[] = [];
    if (p !== null && (Number.isNaN(p) || p < 0)) problemi.push("Il parametro è un numero positivo.");
    if (v.descrizione.trim().length < 3) problemi.push("Descrivi il vincolo (almeno 3 caratteri).");
    if (problemi.length) return setErrori(problemi);
    onFatto({ ...v, descrizione: v.descrizione.trim(), parametro: p });
  };
  return (
    <CorniceEditor errori={errori} onFatto={fatto} onAnnulla={onAnnulla}>
      <div className="grid gap-4 sm:grid-cols-2">
        <Select<TipoVincoloPartenariato>
          etichetta="Tipo di vincolo"
          valore={v.tipo}
          opzioni={Object.entries(TIPO_VINCOLO) as Array<[TipoVincoloPartenariato, string]>}
          onChange={(t) => setV({ ...v, tipo: t })}
        />
        <Select<MomentoRegola>
          etichetta="Quando vale"
          valore={v.momento}
          opzioni={MOMENTI.map((m) => [m, nomeMomento(m)])}
          onChange={(m) => setV({ ...v, momento: m })}
        />
      </div>
      <TestoLungo
        etichetta="Descrizione"
        valore={v.descrizione}
        onChange={(t) => setV({ ...v, descrizione: t })}
        massimo={2000}
        righe={3}
        required
      />
      <TextField
        label="Parametro (facoltativo)"
        helper="Un numero, se il vincolo ne ha uno (per esempio i paesi distinti)."
        inputMode="decimal"
        value={parametro}
        onChange={(e) => setParametro(e.target.value)}
      />
    </CorniceEditor>
  );
}

function EditorDocumento({
  iniziale,
  onFatto,
  onAnnulla,
}: {
  iniziale: ValoriDocumento;
  onFatto: (v: ValoriDocumento) => void;
  onAnnulla: () => void;
}) {
  const [v, setV] = useState(iniziale);
  const [errori, setErrori] = useState<string[]>([]);
  const fatto = () => {
    if (v.descrizione.trim().length < 3) return setErrori(["Descrivi il documento (almeno 3 caratteri)."]);
    onFatto({ ...v, descrizione: v.descrizione.trim() });
  };
  return (
    <CorniceEditor errori={errori} onFatto={fatto} onAnnulla={onAnnulla}>
      <div className="grid gap-4 sm:grid-cols-2">
        <Select<TipoDocumentoRichiesto>
          etichetta="Documento"
          valore={v.tipo}
          opzioni={Object.entries(TIPO_DOCUMENTO) as Array<[TipoDocumentoRichiesto, string]>}
          onChange={(t) => setV({ ...v, tipo: t })}
        />
        <Select<MomentoRegola>
          etichetta="Quando serve"
          valore={v.momento}
          opzioni={MOMENTI.map((m) => [m, nomeMomento(m)])}
          onChange={(m) => setV({ ...v, momento: m })}
        />
      </div>
      <TestoLungo
        etichetta="Descrizione"
        valore={v.descrizione}
        onChange={(t) => setV({ ...v, descrizione: t })}
        massimo={2000}
        righe={3}
        required
      />
    </CorniceEditor>
  );
}

/** Numero di soggetti: il testo scritto resta com'è; al genitore arriva il
 *  numero (NaN se non valido, lo segnala il controllo prima dell'invio). */
function CampoConteggio({
  etichetta,
  iniziale,
  disabled,
  onChange,
}: {
  etichetta: string;
  iniziale: number | null;
  disabled: boolean;
  onChange: (n: number | null) => void;
}) {
  const [testo, setTesto] = useState(iniziale === null ? "" : String(iniziale));
  return (
    <TextField
      label={etichetta}
      inputMode="numeric"
      disabled={disabled}
      value={testo}
      onChange={(e) => {
        setTesto(e.target.value);
        onChange(numeroDa(e.target.value, true));
      }}
    />
  );
}

// ---- Blocchi ---------------------------------------------------------------------------

function Blocco({
  titolo,
  descrizione,
  children,
  azione,
}: {
  titolo: string;
  descrizione?: string;
  children: ReactNode;
  azione?: ReactNode;
}) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="space-y-2">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3 id={id} className="text-sm font-semibold text-slate-800">
            {titolo}
          </h3>
          {descrizione && <p className="text-xs text-slate-500">{descrizione}</p>}
        </div>
        {azione}
      </div>
      {children}
    </section>
  );
}

type Modifica = { sezione: SezioneLista; chiave: string; nuova: boolean } | null;

/** Passo 2: le regole del bando confermate, corrette o aggiunte dal creatore
 *  (snapshot della call: matching e validatore leggono solo questo). */
export function PassoRegole({ call, onAvanti, onIndietro, onDirty }: PassoProps) {
  const pb = usePartenariatoBando(call.bando.slug);
  const avvia = useAvviaAnalisiPartenariato(call.bando.slug);
  const conferma = useConfermaRegole(call.id);
  const aggiorna = useAggiornaCall(call.id);
  const { data: vocabolario } = usePartenariatiVocabolario();
  const { data: lookups } = useLookups();
  const sola = call.stato !== "bozza";

  const estratte = pb.data?.regole ?? null;
  const [lavoro, setLavoro] = useState<RegoleLavoro | null>(null);
  const [esclusivita, setEsclusivita] = useState(call.esclusivita);
  const [modifica, setModifica] = useState<Modifica>(null);
  const [errori, setErrori] = useState<string[]>([]);
  const base = useRef<string | null>(null);
  const firmaEstratte = estratte ? firma(estratte) : null;

  const firmaAttuale = lavoro ? firma({ s: snapshotDa(lavoro), esclusivita }) : null;
  const dirty = !sola && !!lavoro && firmaAttuale !== base.current;

  // Stato di lavoro: al primo arrivo delle regole e quando cambiano (analisi
  // finita), purché non ci siano modifiche in corso.
  useEffect(() => {
    if (pb.isPending) return;
    if (lavoro && dirty) return;
    const l = iniziali(estratte, call.regole_partenariato);
    const escl = call.regole_confermate_at
      ? call.esclusivita
      : l.vincoli.some((v) => v.incluso && v.valori.tipo === "esclusivita_partenariato");
    base.current = firma({ s: snapshotDa(l), esclusivita: escl });
    setLavoro(l);
    setEsclusivita(escl);
    // Di proposito solo questi: si riparte dai dati quando arrivano le regole
    // o cambia lo snapshot salvato, mai a ogni modifica locale.
  }, [pb.isPending, firmaEstratte, call.regole_confermate_at]);

  useEffect(() => onDirty(dirty), [dirty, onDirty]);

  const nomeRegione = useMemo(() => {
    const m = new Map((lookups?.regioni ?? []).map((r) => [r.id, r.nome]));
    return (id: number) => m.get(id) ?? String(id);
  }, [lookups]);

  if (pb.isPending || !lavoro) {
    return (
      <div className="space-y-3" aria-hidden>
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const set = (l: RegoleLavoro) => setLavoro(l);
  const aggiornaLista = <K extends SezioneLista>(
    sezione: K,
    fn: (voci: RegoleLavoro[K]) => RegoleLavoro[K],
  ) => set({ ...lavoro, [sezione]: fn(lavoro[sezione]) });
  const includi = (sezione: SezioneLista, chiave: string, incluso: boolean) =>
    aggiornaLista(sezione, (voci) =>
      (voci as Voce<unknown>[]).map((v) => (v.chiave === chiave ? { ...v, incluso } : v)) as never,
    );
  const ripristina = (sezione: SezioneLista, chiave: string) =>
    aggiornaLista(sezione, (voci) =>
      (voci as Voce<unknown>[]).map((v) =>
        v.chiave === chiave && v.originale !== null ? { ...v, valori: v.originale } : v,
      ) as never,
    );
  const rimuovi = (sezione: SezioneLista, chiave: string) =>
    aggiornaLista(sezione, (voci) => (voci as Voce<unknown>[]).filter((v) => v.chiave !== chiave) as never);
  const applica = (sezione: SezioneLista, chiave: string, valori: unknown) => {
    aggiornaLista(sezione, (voci) =>
      (voci as Voce<unknown>[]).map((v) => (v.chiave === chiave ? { ...v, valori, incluso: true } : v)) as never,
    );
    setModifica(null);
  };
  const annullaModifica = () => {
    if (modifica?.nuova) rimuovi(modifica.sezione, modifica.chiave);
    setModifica(null);
  };
  const aggiungi = <K extends SezioneLista>(sezione: K, prefisso: string, crea: (id: string) => unknown) => {
    const id = nuovoId(lavoro[sezione] as Voce<unknown>[], prefisso);
    const voce: Voce<unknown> = {
      chiave: id,
      incluso: true,
      valori: crea(id),
      originale: null,
      citazione: null,
      confermabile: false,
      avvisi: [],
    };
    aggiornaLista(sezione, (voci) => [...(voci as Voce<unknown>[]), voce] as never);
    setModifica({ sezione, chiave: id, nuova: true });
  };
  const inModifica = (sezione: SezioneLista, chiave: string) =>
    modifica?.sezione === sezione && modifica.chiave === chiave;

  // Le forme del vocabolario più quelle già nelle regole (anche se il
  // vocabolario non è ancora arrivato).
  const codiciForme = [
    ...new Set<string>([
      ...(vocabolario?.forme ?? []).map((f) => f.codice),
      ...lavoro.forme.map((f) => f.chiave),
    ]),
  ];
  const formaVoce = (codice: string) => lavoro.forme.find((f) => f.chiave === codice);
  const alternaForma = (codice: string, incluso: boolean) => {
    const esistente = formaVoce(codice);
    if (esistente) {
      if (!incluso && esistente.originale === null) {
        set({ ...lavoro, forme: lavoro.forme.filter((f) => f.chiave !== codice) });
      } else {
        set({
          ...lavoro,
          forme: lavoro.forme.map((f) => (f.chiave === codice ? { ...f, incluso } : f)),
        });
      }
      return;
    }
    if (!incluso) return;
    set({
      ...lavoro,
      forme: [
        ...lavoro.forme,
        {
          chiave: codice,
          incluso: true,
          valori: { forma: codice as never, note: null },
          originale: null,
          citazione: null,
          confermabile: false,
          avvisi: [],
        },
      ],
    });
  };

  const salva = async () => {
    setErrori([]);
    if (sola) {
      onAvanti();
      return;
    }
    if (modifica) {
      setErrori(["Completa o annulla la voce che stai modificando."]);
      return;
    }
    const problemi = erroriLavoro(lavoro);
    if (problemi.length) {
      setErrori(problemi);
      return;
    }
    try {
      if (dirty || !call.regole_confermate_at) {
        await conferma.mutateAsync({ regole: snapshotDa(lavoro), esclusivita });
      }
      const passo = avanzamento(call, 3);
      if (Object.keys(passo).length) await aggiorna.mutateAsync(passo);
      onAvanti();
    } catch {
      // mostrato nella barra
    }
  };

  const erroreServer = conferma.isError
    ? apiErrorMessage(conferma.error)
    : aggiorna.isError
      ? apiErrorMessage(aggiorna.error)
      : null;

  const statoEstrazione = pb.data;
  const inCorso = statoEstrazione ? analisiInCorso(statoEstrazione) : false;

  return (
    <div className="space-y-4">
      <Card className="space-y-3 p-5">
        <p className="text-sm text-slate-700">
          Queste sono le regole di partenariato che abbiamo letto nel bando. Conferma quelle giuste,
          correggi o aggiungi le altre: sono le regole con cui la piattaforma cercherà i partner e
          controllerà il consorzio.
        </p>
        <p className="text-xs text-slate-500">{PARTENARIATO_COPY.disclaimer}</p>
        {call.regole_confermate_at && (
          <p className="text-xs text-slate-500">
            Confermate il {formatDate(call.regole_confermate_at)}.
          </p>
        )}
        {sola && (
          <p className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">
            Dopo la pubblicazione le regole non si cambiano più.
          </p>
        )}
        <div aria-live="polite">
          {inCorso && statoEstrazione ? (
            <Avanzamento fase={statoEstrazione.fase} />
          ) : !estratte && !sola ? (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              <p>
                {statoEstrazione?.stato === "nessun_segnale"
                  ? "Nel testo del bando non abbiamo trovato riferimenti a partenariati: puoi indicare tu le regole."
                  : statoEstrazione?.stato === "errore"
                    ? "L'ultima lettura delle regole non è riuscita: puoi riprovare o indicarle tu."
                    : "Le regole del bando non sono ancora state lette: avvia la lettura o indicale tu."}
              </p>
              {statoEstrazione?.puo_avviare && (
                <Button
                  variant="secondary"
                  size="sm"
                  loading={avvia.isPending}
                  onClick={() => avvia.mutate({ forza: statoEstrazione.stato === "nessun_segnale" })}
                >
                  Leggi le regole del bando
                </Button>
              )}
            </div>
          ) : null}
          {avvia.isError && (
            <p className="mt-2 text-sm text-red-700" role="alert">
              {apiErrorMessage(avvia.error)}
            </p>
          )}
        </div>
      </Card>

      <Card className="space-y-6 p-5">
        <Blocco titolo="Modalità di partecipazione">
          <div className="flex flex-wrap items-end gap-3">
            <div className="w-full max-w-xs">
              <Select<ModalitaPartenariato>
                etichetta="Il bando"
                valore={lavoro.modalita.valori.valore}
                opzioni={MODALITA_SCELTE.map((m) => [m, PARTENARIATO_COPY.modalita[m]])}
                onChange={(m) => set({ ...lavoro, modalita: { ...lavoro.modalita, valori: { valore: m } } })}
                disabled={sola}
              />
            </div>
            <BadgeOrigine voce={lavoro.modalita} />
          </div>
          <Citazione citazione={lavoro.modalita.citazione} />
        </Blocco>

        <Blocco
          titolo="Forme di aggregazione ammesse"
          descrizione="Spunta le forme con cui si può partecipare."
        >
          <ul className="grid gap-2 sm:grid-cols-2">
            {codiciForme.map((codice) => {
              const voce = formaVoce(codice);
              return (
                <li key={codice} className="rounded-lg border border-slate-200 px-3 py-2">
                  <label className="flex cursor-pointer items-start gap-2 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      className="mt-0.5 size-4 shrink-0 cursor-pointer accent-brand-500 disabled:cursor-not-allowed"
                      checked={!!voce?.incluso}
                      disabled={sola}
                      onChange={(e) => alternaForma(codice, e.target.checked)}
                    />
                    <span className="min-w-0">
                      {etichettaForma(codice, vocabolario)}
                      {voce && (
                        <span className="ml-1.5 inline-block align-middle">
                          <BadgeOrigine voce={voce} />
                        </span>
                      )}
                    </span>
                  </label>
                  {voce && <Citazione citazione={voce.citazione} />}
                </li>
              );
            })}
          </ul>
        </Blocco>

        <Blocco titolo="Costituzione del raggruppamento">
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                className="size-4 cursor-pointer accent-brand-500"
                checked={lavoro.costituzione.incluso}
                disabled={sola}
                onChange={(e) =>
                  set({ ...lavoro, costituzione: { ...lavoro.costituzione, incluso: e.target.checked } })
                }
              />
              Il bando lo indica
            </label>
            <BadgeOrigine voce={lavoro.costituzione} />
          </div>
          {lavoro.costituzione.incluso && (
            <div className="max-w-xl">
              <Select
                etichetta="Quando va costituito"
                valore={lavoro.costituzione.valori.valore}
                opzioni={Object.entries(COSTITUZIONE) as Array<[typeof lavoro.costituzione.valori.valore, string]>}
                onChange={(c) => set({ ...lavoro, costituzione: { ...lavoro.costituzione, valori: { valore: c } } })}
                disabled={sola}
              />
            </div>
          )}
          <Citazione citazione={lavoro.costituzione.citazione} />
        </Blocco>

        <Blocco titolo="Numero di soggetti nel partenariato" descrizione="Capofila compreso. Lascia vuoto se il bando non lo dice.">
          <div className="grid max-w-md gap-4 sm:grid-cols-2">
            {(["partner_min", "partner_max"] as const).map((k) => {
              const voce = lavoro[k];
              return (
                <div key={`${k}-${base.current}`} className="space-y-1">
                  <CampoConteggio
                    etichetta={k === "partner_min" ? "Almeno" : "Al massimo"}
                    iniziale={voce.incluso ? voce.valori.valore : null}
                    disabled={sola}
                    onChange={(n) =>
                      set({
                        ...lavoro,
                        [k]: n === null ? { ...voce, incluso: false } : { ...voce, incluso: true, valori: { valore: n } },
                      })
                    }
                  />
                  <BadgeOrigine voce={voce} />
                  <Citazione citazione={voce.citazione} />
                </div>
              );
            })}
          </div>
        </Blocco>

        <Blocco
          titolo="Chi deve far parte del partenariato"
          azione={
            !sola && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() =>
                  aggiungi("composizione", "cx", (id) => ({
                    id,
                    tipo_soggetto: "impresa",
                    tipo_soggetto_testo: null,
                    minimo: 1,
                    massimo: null,
                    ruolo: "qualsiasi",
                    regioni: [],
                    paesi: [],
                    vincolo_territoriale: null,
                  }))
                }
              >
                <Plus className="size-4" aria-hidden />
                Aggiungi
              </Button>
            )
          }
        >
          {lavoro.composizione.length === 0 ? (
            <p className="text-sm text-slate-500">Nessun vincolo sulla composizione.</p>
          ) : (
            <ul className="space-y-2">
              {lavoro.composizione.map((v) => (
                <RigaVoce
                  key={v.chiave}
                  voce={v}
                  sola={sola}
                  titolo={titoloComposizione(v.valori, vocabolario)}
                  dettaglio={
                    v.valori.regioni.length || v.valori.vincolo_territoriale
                      ? [
                          v.valori.regioni.length ? `Regioni: ${v.valori.regioni.map(nomeRegione).join(", ")}` : null,
                          v.valori.vincolo_territoriale,
                        ]
                          .filter(Boolean)
                          .join(" · ")
                      : undefined
                  }
                  onIncluso={(inc) => includi("composizione", v.chiave, inc)}
                  onModifica={() => setModifica({ sezione: "composizione", chiave: v.chiave, nuova: false })}
                  onRipristina={() => ripristina("composizione", v.chiave)}
                  onRimuovi={() => rimuovi("composizione", v.chiave)}
                >
                  {inModifica("composizione", v.chiave) && (
                    <EditorComposizione
                      iniziale={v.valori}
                      onFatto={(val) => applica("composizione", v.chiave, val)}
                      onAnnulla={annullaModifica}
                    />
                  )}
                </RigaVoce>
              ))}
            </ul>
          )}
        </Blocco>

        <Blocco
          titolo="Quote di partecipazione"
          azione={
            !sola && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() =>
                  aggiungi("quote", "qx", (id) => ({
                    id,
                    ambito: "per_partner",
                    categoria: null,
                    min_percentuale: null,
                    max_percentuale: null,
                    base_calcolo: "costo_totale_progetto",
                    effetto_violazione: "non_indicato",
                  }))
                }
              >
                <Plus className="size-4" aria-hidden />
                Aggiungi
              </Button>
            )
          }
        >
          {lavoro.quote.length === 0 ? (
            <p className="text-sm text-slate-500">Nessuna quota indicata.</p>
          ) : (
            <ul className="space-y-2">
              {lavoro.quote.map((v) => (
                <RigaVoce
                  key={v.chiave}
                  voce={v}
                  sola={sola}
                  titolo={titoloQuota(v.valori, vocabolario)}
                  dettaglio={EFFETTI_QUOTA[v.valori.effetto_violazione] !== "Non indicato" ? `Se non è rispettata: ${EFFETTI_QUOTA[v.valori.effetto_violazione].toLowerCase()}` : undefined}
                  onIncluso={(inc) => includi("quote", v.chiave, inc)}
                  onModifica={() => setModifica({ sezione: "quote", chiave: v.chiave, nuova: false })}
                  onRipristina={() => ripristina("quote", v.chiave)}
                  onRimuovi={() => rimuovi("quote", v.chiave)}
                >
                  {inModifica("quote", v.chiave) && (
                    <EditorQuota
                      iniziale={v.valori}
                      onFatto={(val) => applica("quote", v.chiave, val)}
                      onAnnulla={annullaModifica}
                    />
                  )}
                </RigaVoce>
              ))}
            </ul>
          )}
        </Blocco>

        <Blocco
          titolo="Vincoli"
          azione={
            !sola && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() =>
                  aggiungi("vincoli", "vx", (id) => ({
                    id,
                    tipo: "altro",
                    descrizione: "",
                    parametro: null,
                    momento: "domanda",
                  }))
                }
              >
                <Plus className="size-4" aria-hidden />
                Aggiungi
              </Button>
            )
          }
        >
          {lavoro.vincoli.length === 0 ? (
            <p className="text-sm text-slate-500">Nessun vincolo indicato.</p>
          ) : (
            <ul className="space-y-2">
              {lavoro.vincoli.map((v) => (
                <RigaVoce
                  key={v.chiave}
                  voce={v}
                  sola={sola}
                  titolo={TIPO_VINCOLO[v.valori.tipo] ?? v.valori.tipo}
                  dettaglio={
                    <>
                      <span className="whitespace-pre-line">{v.valori.descrizione}</span>
                      {MOMENTO[v.valori.momento] && (
                        <span className="block text-xs text-slate-500">{MOMENTO[v.valori.momento]}</span>
                      )}
                    </>
                  }
                  onIncluso={(inc) => includi("vincoli", v.chiave, inc)}
                  onModifica={() => setModifica({ sezione: "vincoli", chiave: v.chiave, nuova: false })}
                  onRipristina={() => ripristina("vincoli", v.chiave)}
                  onRimuovi={() => rimuovi("vincoli", v.chiave)}
                >
                  {inModifica("vincoli", v.chiave) && (
                    <EditorVincolo
                      iniziale={v.valori}
                      onFatto={(val) => applica("vincoli", v.chiave, val)}
                      onAnnulla={annullaModifica}
                    />
                  )}
                </RigaVoce>
              ))}
            </ul>
          )}
        </Blocco>

        <Blocco
          titolo="Requisiti economici"
          descrizione="Solo quelli letti nel bando con il passaggio ritrovato: si confermano così come sono o si tolgono."
        >
          {lavoro.regole_finanziarie.length === 0 ? (
            <p className="text-sm text-slate-500">Nessun requisito economico letto nel bando.</p>
          ) : (
            <ul className="space-y-2">
              {lavoro.regole_finanziarie.map((v) => (
                <RigaVoce
                  key={v.chiave}
                  voce={v}
                  sola={sola}
                  sezione="regole_finanziarie"
                  titolo={v.valori.descrizione}
                  dettaglio={formulaFinanziaria(v.valori) ?? undefined}
                  nonIncludibile="Il passaggio del bando non è stato ritrovato alla lettera: una regola economica non si può inserire a mano. Controllala tu sul bando."
                  onIncluso={(inc) => includi("regole_finanziarie", v.chiave, inc)}
                />
              ))}
            </ul>
          )}
        </Blocco>

        <Blocco
          titolo="Documenti richiesti al partenariato"
          azione={
            !sola && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() =>
                  aggiungi("documenti_richiesti", "dx", (id) => ({
                    id,
                    tipo: "accordo_partenariato",
                    descrizione: "",
                    momento: "domanda",
                  }))
                }
              >
                <Plus className="size-4" aria-hidden />
                Aggiungi
              </Button>
            )
          }
        >
          {lavoro.documenti_richiesti.length === 0 ? (
            <p className="text-sm text-slate-500">Nessun documento indicato.</p>
          ) : (
            <ul className="space-y-2">
              {lavoro.documenti_richiesti.map((v) => (
                <RigaVoce
                  key={v.chiave}
                  voce={v}
                  sola={sola}
                  titolo={TIPO_DOCUMENTO[v.valori.tipo] ?? v.valori.tipo}
                  dettaglio={<span className="whitespace-pre-line">{v.valori.descrizione}</span>}
                  onIncluso={(inc) => includi("documenti_richiesti", v.chiave, inc)}
                  onModifica={() =>
                    setModifica({ sezione: "documenti_richiesti", chiave: v.chiave, nuova: false })
                  }
                  onRipristina={() => ripristina("documenti_richiesti", v.chiave)}
                  onRimuovi={() => rimuovi("documenti_richiesti", v.chiave)}
                >
                  {inModifica("documenti_richiesti", v.chiave) && (
                    <EditorDocumento
                      iniziale={v.valori}
                      onFatto={(val) => applica("documenti_richiesti", v.chiave, val)}
                      onAnnulla={annullaModifica}
                    />
                  )}
                </RigaVoce>
              ))}
            </ul>
          )}
        </Blocco>

        <Blocco titolo="Esclusività">
          <label
            className={cn(
              "flex items-start gap-2 text-sm text-slate-700",
              sola ? "cursor-not-allowed" : "cursor-pointer",
            )}
          >
            <input
              type="checkbox"
              className="mt-0.5 size-4 shrink-0 cursor-pointer accent-brand-500 disabled:cursor-not-allowed"
              checked={esclusivita}
              disabled={sola}
              onChange={(e) => setEsclusivita(e.target.checked)}
            />
            <span>
              Il bando vieta di partecipare a più di un partenariato
              <span className="block text-xs text-slate-500">
                Chi entra nella tua call non potrà far parte di altre call sullo stesso bando.
              </span>
            </span>
          </label>
        </Blocco>

        {errori.length > 0 && (
          <div className="flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            <ul className="space-y-0.5">
              {errori.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          </div>
        )}
        <BarraPasso
          onIndietro={onIndietro}
          onAvanti={() => void salva()}
          etichettaAvanti={sola ? "Continua" : "Conferma le regole e continua"}
          inCorso={conferma.isPending || aggiorna.isPending}
          errore={erroreServer}
        />
      </Card>
    </div>
  );
}
