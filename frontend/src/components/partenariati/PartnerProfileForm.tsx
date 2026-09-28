import { Building2, Plus, X } from "lucide-react";
import { useId, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { useLookups } from "../../hooks/useLookups";
import { usePartenariatiVocabolario } from "../../hooks/usePartenariatiVocabolario";
import { useSalvaPartnerProfile } from "../../hooks/usePartnerProfile";
import { apiErrorMessage } from "../../lib/api";
import { cn } from "../../lib/cn";
import { PARTNER_COPY } from "../../lib/copy";
import { nomePaese, paesiOrdinati } from "../../lib/paesi";
import type {
  FormaProfiloPartner,
  LookupItem,
  PartnerProfileInput,
  RuoloPartner,
  TipoSoggettoPartenariato,
} from "../../types";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { TextareaField } from "../ui/Field";
import { TagSelect } from "../ui/TagSelect";
import { Skeleton } from "../ui/states";
import { EsperienzeEditor, erroreEsperienza } from "./EsperienzeEditor";

// ---- Limiti e regole (gli stessi dello schema del server) ------------------

export const MAX_COMPETENZE = 15;
const MAX_COMPETENZE_LIBERE = 10;
const MAX_COMPETENZA_LIBERA = 80;
const MAX_TIPI_SOGGETTO = 8;
const MAX_SETTORI = 20;
const MAX_REGIONI = 21;
const MAX_PAESI = 30;
const MAX_CERTIFICAZIONI = 20;
const MAX_CERTIFICAZIONE = 200;
const MAX_CATEGORIE_ESCLUSE = 50;
const MAX_TESTO_LUNGO = 2000;
const SOGLIA_DESCRIZIONE = 80;

/** Tipi di soggetto che vengono SOLO dal Registro Imprese: non si
 *  dichiarano (il server risponderebbe 400). */
export const TIPI_SOGGETTO_DA_REGISTRO: ReadonlySet<TipoSoggettoPartenariato> = new Set([
  "micro_impresa",
  "piccola_impresa",
  "media_impresa",
  "grande_impresa",
  "pmi",
  "startup_innovativa",
  "pmi_innovativa",
  "impresa_artigiana",
  "cooperativa",
]);

const RUOLI: Array<{ codice: RuoloPartner; etichetta: string; nota: string }> = [
  { codice: "capofila", etichetta: "Capofila", nota: "Guidi il progetto e tieni i rapporti con l'ente." },
  { codice: "partner", etichetta: "Partner", nota: "Partecipi al progetto guidato da un'altra azienda." },
];

/** Firma per il confronto «ci sono modifiche?»: gli elenchi di codici e di
 *  id valgono come insiemi (spuntare e rispuntare non è una modifica). */
export function firmaProfilo(p: PartnerProfileInput): string {
  const ordinati = <T,>(v: T[]) => [...v].map(String).sort();
  return JSON.stringify({
    ...p,
    descrizione_competenze: p.descrizione_competenze?.trim() || null,
    infrastrutture: p.infrastrutture?.trim() || null,
    competenze: ordinati(p.competenze),
    tipi_soggetto: ordinati(p.tipi_soggetto),
    ruoli_disponibili: ordinati(p.ruoli_disponibili),
    settori_interesse: ordinati(p.settori_interesse),
    regioni_interesse: ordinati(p.regioni_interesse),
    paesi_interesse: ordinati(p.paesi_interesse),
    forme_accettate: ordinati(p.forme_accettate),
    categorie_bando_escluse: ordinati(p.categorie_bando_escluse),
  });
}

/** Corpo del PUT: testi ripuliti, righe vuote tolte, solo i campi dello
 *  schema (il server rifiuta quelli in più). */
function payload(p: PartnerProfileInput): PartnerProfileInput {
  const testo = (v: string | null) => (v?.trim() ? v.trim() : null);
  return {
    descrizione_competenze: testo(p.descrizione_competenze),
    competenze: p.competenze,
    competenze_libere: p.competenze_libere,
    tipi_soggetto: p.tipi_soggetto,
    ruoli_disponibili: p.ruoli_disponibili,
    settori_interesse: p.settori_interesse,
    regioni_interesse: p.regioni_interesse,
    paesi_interesse: p.paesi_interesse,
    forme_accettate: p.forme_accettate,
    esperienze: p.esperienze.map((e) => ({
      programma: e.programma.trim(),
      programma_id: e.programma_id,
      anno: e.anno,
      ruolo: e.ruolo,
      titolo: testo(e.titolo),
      esito: e.esito,
    })),
    certificazioni: p.certificazioni,
    infrastrutture: testo(p.infrastrutture),
    accetta_inviti: p.accetta_inviti,
    categorie_bando_escluse: p.categorie_bando_escluse,
  };
}

/** Aggiunge o toglie un codice mantenendo l'ordine di riferimento (quello
 *  del vocabolario), così l'elenco salvato è stabile. */
function alterna<T extends string>(scelti: T[], codice: T, ordine: readonly T[]): T[] {
  const insieme = new Set(scelti);
  if (insieme.has(codice)) insieme.delete(codice);
  else insieme.add(codice);
  const noti = ordine.filter((c) => insieme.has(c));
  const altri = scelti.filter((c) => insieme.has(c) && !ordine.includes(c));
  return [...noti, ...altri];
}

// ---- Blocchi del form ---------------------------------------------------------

function Sezione({
  titolo,
  descrizione,
  children,
}: {
  titolo: string;
  descrizione?: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <Card className="p-5">
      <section aria-labelledby={id}>
        <h3 id={id} className="font-display text-base font-semibold text-slate-900">
          {titolo}
        </h3>
        {descrizione && <p className="mt-0.5 text-sm text-slate-500">{descrizione}</p>}
        <div className="mt-4 space-y-5">{children}</div>
      </section>
    </Card>
  );
}

interface OpzioneCheckbox<T extends string> {
  codice: T;
  etichetta: string;
  nota?: string;
}

/** Gruppo di checkbox con legenda: al limite le voci non scelte si
 *  disabilitano (quelle scelte restano togliibili). */
function GruppoCheckbox<T extends string>({
  legenda,
  opzioni,
  scelti,
  onToggle,
  massimo,
  minimo = 0,
  nota,
  colonne = 2,
}: {
  legenda: string;
  opzioni: OpzioneCheckbox<T>[];
  scelti: T[];
  onToggle: (codice: T) => void;
  massimo?: number;
  minimo?: number;
  nota?: string;
  colonne?: 1 | 2;
}) {
  const idNota = useId();
  const pieno = massimo !== undefined && scelti.length >= massimo;
  return (
    <fieldset aria-describedby={nota ? idNota : undefined}>
      <legend className="text-sm font-medium text-slate-700">{legenda}</legend>
      {nota && (
        <p id={idNota} className="mt-0.5 text-xs text-slate-500">
          {nota}
        </p>
      )}
      <div className={cn("mt-2 grid gap-x-4 gap-y-1.5", colonne === 2 && "sm:grid-cols-2")}>
        {opzioni.map((o) => {
          const scelto = scelti.includes(o.codice);
          const bloccato = (!scelto && pieno) || (scelto && scelti.length <= minimo);
          return (
            <label
              key={o.codice}
              className={cn(
                "flex items-start gap-2 text-sm",
                bloccato ? "cursor-not-allowed text-slate-400" : "cursor-pointer text-slate-700",
              )}
            >
              <input
                type="checkbox"
                className="mt-0.5 size-4 shrink-0 cursor-pointer accent-brand-500 disabled:cursor-not-allowed"
                checked={scelto}
                disabled={bloccato}
                onChange={() => onToggle(o.codice)}
              />
              <span>
                {o.etichetta}
                {o.nota && <span className="block text-xs text-slate-500">{o.nota}</span>}
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

/** Voci brevi scritte a mano (competenze aggiuntive, certificazioni): campo
 *  + «Aggiungi», con Invio come scorciatoia; le voci sono chip rimovibili. */
function VociLibere({
  etichetta,
  aiuto,
  voci,
  onChange,
  massimo,
  lunghezzaMassima,
  segnaposto,
}: {
  etichetta: string;
  aiuto?: string;
  voci: string[];
  onChange: (voci: string[]) => void;
  massimo: number;
  lunghezzaMassima: number;
  segnaposto?: string;
}) {
  const id = useId();
  const [bozza, setBozza] = useState("");
  const [avviso, setAvviso] = useState<string | null>(null);
  const pieno = voci.length >= massimo;

  const aggiungi = () => {
    const voce = bozza.split(/\s+/).filter(Boolean).join(" ");
    if (!voce) return;
    if (voci.some((v) => v.toLowerCase() === voce.toLowerCase())) {
      setAvviso("C'è già");
      return;
    }
    if (pieno) return;
    onChange([...voci, voce]);
    setBozza("");
    setAvviso(null);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      aggiungi();
    }
  };

  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium text-slate-700">
        {etichetta}
      </label>
      {aiuto && (
        <p id={`${id}-aiuto`} className="text-xs text-slate-500">
          {aiuto}
        </p>
      )}
      {voci.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {voci.map((v) => (
            <li key={v}>
              <span className="inline-flex max-w-full items-center gap-1 rounded-full bg-brand-50 py-1 pl-2.5 pr-1 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-200">
                <span className="truncate">{v}</span>
                <button
                  type="button"
                  onClick={() => onChange(voci.filter((x) => x !== v))}
                  aria-label={`Rimuovi «${v}»`}
                  className="cursor-pointer rounded-full p-0.5 transition-colors hover:bg-brand-100 focus-visible:outline-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed"
                >
                  <X className="size-3" aria-hidden />
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="flex max-w-md gap-2">
        <input
          id={id}
          value={bozza}
          maxLength={lunghezzaMassima}
          placeholder={pieno ? `Massimo ${massimo} voci` : segnaposto}
          disabled={pieno}
          aria-describedby={aiuto ? `${id}-aiuto` : undefined}
          onChange={(e) => {
            setBozza(e.target.value);
            setAvviso(null);
          }}
          onKeyDown={onKeyDown}
          className="h-10 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-900 placeholder:text-slate-400 focus:border-brand-500 focus:outline-2 focus:outline-offset-0 focus:outline-brand-500/30 disabled:cursor-not-allowed disabled:bg-slate-50"
        />
        <Button
          variant="secondary"
          onClick={aggiungi}
          disabled={pieno || !bozza.trim()}
          aria-label={`Aggiungi a ${etichetta.toLowerCase()}`}
        >
          <Plus className="size-4" aria-hidden />
          Aggiungi
        </Button>
      </div>
      {avviso && (
        <p className="text-xs text-amber-700" role="status">
          {avviso}
        </p>
      )}
    </div>
  );
}

/** Scelta multipla su una lookup del catalogo (regioni, settori, tipologie):
 *  chip rimovibili + ricerca. */
function SceltaLookup({
  etichetta,
  aiuto,
  opzioni,
  scelti,
  onChange,
  massimo,
}: {
  etichetta: string;
  aiuto?: string;
  opzioni: LookupItem[] | undefined;
  scelti: number[];
  onChange: (scelti: number[]) => void;
  massimo: number;
}) {
  const nomi = useMemo(() => new Map((opzioni ?? []).map((o) => [o.id, o.nome])), [opzioni]);
  const pieno = scelti.length >= massimo;
  const alternaId = (id: number) => {
    if (scelti.includes(id)) onChange(scelti.filter((x) => x !== id));
    else if (!pieno) onChange([...scelti, id]);
  };
  return (
    <div className="space-y-1.5">
      <p className="text-sm font-medium text-slate-700">{etichetta}</p>
      {aiuto && <p className="text-xs text-slate-500">{aiuto}</p>}
      {scelti.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {scelti.map((id) => {
            const nome = nomi.get(id) ?? `#${id}`;
            return (
              <li key={id}>
                <span className="inline-flex max-w-full items-center gap-1 rounded-full bg-brand-50 py-1 pl-2.5 pr-1 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-200">
                  <span className="truncate">{nome}</span>
                  <button
                    type="button"
                    onClick={() => alternaId(id)}
                    aria-label={`Rimuovi ${nome}`}
                    className="cursor-pointer rounded-full p-0.5 transition-colors hover:bg-brand-100 focus-visible:outline-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed"
                  >
                    <X className="size-3" aria-hidden />
                  </button>
                </span>
              </li>
            );
          })}
        </ul>
      )}
      <div className="max-w-md">
        {opzioni ? (
          <TagSelect
            label={etichetta}
            options={opzioni.map((o) => ({ id: o.id, label: o.nome }))}
            values={scelti}
            onToggle={alternaId}
            placeholder={pieno ? `Massimo ${massimo}` : "Cerca e aggiungi…"}
          />
        ) : (
          <Skeleton className="h-10 w-full" />
        )}
      </div>
    </div>
  );
}

/** Paesi d'interesse (codici ISO a due lettere, nomi in italiano). */
function SceltaPaesi({
  scelti,
  onChange,
}: {
  scelti: string[];
  onChange: (paesi: string[]) => void;
}) {
  const id = useId();
  const [paese, setPaese] = useState("");
  const disponibili = useMemo(() => paesiOrdinati().filter((c) => !scelti.includes(c)), [scelti]);
  const pieno = scelti.length >= MAX_PAESI;
  const aggiungi = () => {
    if (!paese || pieno || scelti.includes(paese)) return;
    onChange([...scelti, paese]);
    setPaese("");
  };
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium text-slate-700">
        Paesi d'interesse
      </label>
      <p className="text-xs text-slate-500">Per i bandi europei e internazionali.</p>
      {scelti.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {scelti.map((codice) => (
            <li key={codice}>
              <span className="inline-flex items-center gap-1 rounded-full bg-brand-50 py-1 pl-2.5 pr-1 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-200">
                {nomePaese(codice)}
                <button
                  type="button"
                  onClick={() => onChange(scelti.filter((c) => c !== codice))}
                  aria-label={`Rimuovi ${nomePaese(codice)}`}
                  className="cursor-pointer rounded-full p-0.5 transition-colors hover:bg-brand-100 focus-visible:outline-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed"
                >
                  <X className="size-3" aria-hidden />
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="flex max-w-md gap-2">
        <select
          id={id}
          value={paese}
          disabled={pieno}
          onChange={(e) => setPaese(e.target.value)}
          className="h-10 w-full cursor-pointer rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-900 focus:border-brand-500 focus:outline-2 focus:outline-offset-0 focus:outline-brand-500/30 disabled:cursor-not-allowed disabled:bg-slate-50"
        >
          <option value="">{pieno ? `Massimo ${MAX_PAESI} paesi` : "Scegli un paese…"}</option>
          {disponibili.map((c) => (
            <option key={c} value={c}>
              {nomePaese(c)}
            </option>
          ))}
        </select>
        <Button variant="secondary" onClick={aggiungi} disabled={!paese || pieno}>
          <Plus className="size-4" aria-hidden />
          Aggiungi
        </Button>
      </div>
    </div>
  );
}

function TestoConContatore({
  etichetta,
  aiuto,
  valore,
  onChange,
  righe = 5,
}: {
  etichetta: string;
  aiuto: string;
  valore: string | null;
  onChange: (v: string | null) => void;
  righe?: number;
}) {
  const lunghezza = valore?.length ?? 0;
  return (
    <div>
      <TextareaField
        label={etichetta}
        helper={aiuto}
        rows={righe}
        maxLength={MAX_TESTO_LUNGO}
        value={valore ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
      />
      <p className="mt-1 text-right text-xs text-slate-400 tabular" aria-hidden>
        {lunghezza.toLocaleString("it-IT")} / {MAX_TESTO_LUNGO.toLocaleString("it-IT")}
      </p>
    </div>
  );
}

// ---- Form ----------------------------------------------------------------------

export interface PartnerProfileFormProps {
  /** Stato del form (tenuto dal genitore: la bozza AI lo riempie). */
  valore: PartnerProfileInput;
  onChange: (valore: PartnerProfileInput) => void;
  /** Profilo salvato: base per «modifiche non salvate» e «Annulla». */
  salvato: PartnerProfileInput;
  editable: boolean;
  anonimo: boolean;
  dedotti: TipoSoggettoPartenariato[];
  avvisiAnonimato: string[];
}

/** Editor del profilo partner. Tutto ciò che è qui è visibile alle altre
 *  aziende solo dopo il consenso; contatti e dati che identificano l'azienda
 *  li blocca il server al salvataggio. Barra di salvataggio fissa in basso
 *  (come in Preferenze), solo con modifiche; sola lettura per i membri. */
export function PartnerProfileForm({
  valore,
  onChange,
  salvato,
  editable,
  anonimo,
  dedotti,
  avvisiAnonimato,
}: PartnerProfileFormProps) {
  const vocabolario = usePartenariatiVocabolario();
  const { data: lookups } = useLookups();
  const salva = useSalvaPartnerProfile();
  const [salvatoFlash, setSalvatoFlash] = useState(false);
  const [mostraErrori, setMostraErrori] = useState(false);
  const [erroreLocale, setErroreLocale] = useState<string | null>(null);

  const dirty = editable && firmaProfilo(valore) !== firmaProfilo(salvato);
  const annoCorrente = new Date().getFullYear();

  const set = <K extends keyof PartnerProfileInput>(chiave: K, v: PartnerProfileInput[K]) =>
    onChange({ ...valore, [chiave]: v });

  const competenzeOrdine = useMemo(
    () => (vocabolario.data?.competenze ?? []).map((c) => c.codice),
    [vocabolario.data],
  );
  const areeCompetenze = useMemo(() => {
    const aree = new Map<string, OpzioneCheckbox<string>[]>();
    for (const c of vocabolario.data?.competenze ?? []) {
      const voci = aree.get(c.area) ?? [];
      voci.push({ codice: c.codice, etichetta: c.etichetta });
      aree.set(c.area, voci);
    }
    return [...aree.entries()];
  }, [vocabolario.data]);

  const etichettaTipo = (codice: string) =>
    vocabolario.data?.tipi_soggetto.find((t) => t.codice === codice)?.etichetta ?? codice;
  const tipiDichiarabili = useMemo(
    () =>
      (vocabolario.data?.tipi_soggetto ?? [])
        .filter((t) => !TIPI_SOGGETTO_DA_REGISTRO.has(t.codice) && !dedotti.includes(t.codice))
        .map((t) => ({ codice: t.codice, etichetta: t.etichetta })),
    [vocabolario.data, dedotti],
  );
  const forme = useMemo(
    () =>
      (vocabolario.data?.forme ?? [])
        .filter((f): f is typeof f & { codice: FormaProfiloPartner } => f.codice !== "altra")
        .map((f) => ({ codice: f.codice, etichetta: f.etichetta })),
    [vocabolario.data],
  );

  const handleSalva = async () => {
    setSalvatoFlash(false);
    setErroreLocale(null);
    const righeIncomplete = valore.esperienze.filter((e) => erroreEsperienza(e, annoCorrente));
    if (righeIncomplete.length > 0) {
      setMostraErrori(true);
      setErroreLocale("Completa le esperienze evidenziate prima di salvare.");
      return;
    }
    try {
      const risposta = await salva.mutateAsync(payload(valore));
      // Il server normalizza (spazi, doppioni, sigle dei paesi): il form
      // riparte dalla versione salvata, così non resta «modificato».
      onChange(risposta.profilo);
      setMostraErrori(false);
      setSalvatoFlash(true);
      window.setTimeout(() => setSalvatoFlash(false), 3000);
    } catch {
      // mostrato nella barra
    }
  };

  const annulla = () => {
    onChange(salvato);
    setMostraErrori(false);
    setErroreLocale(null);
    salva.reset();
  };

  // Un salvataggio fallito lascia il form modificato: l'errore conta finché
  // ci sono modifiche da salvare.
  const erroreBarra = dirty
    ? (erroreLocale ?? (salva.isError ? apiErrorMessage(salva.error) : null))
    : null;
  const barraVisibile = editable && (dirty || salvatoFlash);

  return (
    <div className={cn(barraVisibile && "pb-24")}>
      <fieldset disabled={!editable} className="min-w-0 space-y-4">
        <legend className="sr-only">Profilo partner</legend>

        {anonimo && avvisiAnonimato.length > 0 && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <p className="font-medium">
              Nei testi c'è qualcosa che potrebbe far riconoscere l'azienda anche se resta anonima:
            </p>
            <ul className="mt-1 list-disc pl-5">
              {avvisiAnonimato.map((a, i) => (
                <li key={i}>{a}</li>
              ))}
            </ul>
            <p className="mt-1 text-xs">Non blocca il salvataggio: valuta tu se toglierlo.</p>
          </div>
        )}

        <Sezione
          titolo="Competenze"
          descrizione="Cosa sa fare l'azienda: è la base con cui le altre aziende ti trovano."
        >
          <TestoConContatore
            etichetta="Descrizione delle competenze"
            aiuto={`Qualche riga su cosa fate e per chi (almeno ${SOGLIA_DESCRIZIONE} caratteri). Niente email, telefoni o siti: i contatti si scambiano solo dopo che accetti.`}
            valore={valore.descrizione_competenze}
            onChange={(v) => set("descrizione_competenze", v)}
          />
          {vocabolario.isPending ? (
            <Skeleton className="h-40 w-full" />
          ) : vocabolario.isError ? (
            <p className="text-sm text-red-700" role="alert">
              {apiErrorMessage(vocabolario.error, "Impossibile caricare l'elenco delle competenze.")}
            </p>
          ) : (
            <div className="space-y-4">
              <p className="text-sm text-slate-600">
                Competenze scelte:{" "}
                <span className="font-medium tabular">
                  {valore.competenze.length} di {MAX_COMPETENZE}
                </span>
              </p>
              {areeCompetenze.map(([area, opzioni]) => (
                <GruppoCheckbox
                  key={area}
                  legenda={area}
                  opzioni={opzioni}
                  scelti={valore.competenze}
                  massimo={MAX_COMPETENZE}
                  onToggle={(codice) =>
                    set("competenze", alterna(valore.competenze, codice, competenzeOrdine))
                  }
                />
              ))}
            </div>
          )}
          <VociLibere
            etichetta="Altre competenze"
            aiuto="Se manca qualcosa nell'elenco, scrivila tu (voci brevi)."
            voci={valore.competenze_libere}
            onChange={(v) => set("competenze_libere", v)}
            massimo={MAX_COMPETENZE_LIBERE}
            lunghezzaMassima={MAX_COMPETENZA_LIBERA}
            segnaposto="Es. stampa 3D di metalli"
          />
        </Sezione>

        <Sezione
          titolo="Tipo di soggetto"
          descrizione="Dimensione e qualifiche vengono dal Registro Imprese; il resto lo dichiari tu."
        >
          <div>
            <p className="text-sm font-medium text-slate-700">Dal Registro Imprese</p>
            {dedotti.length > 0 ? (
              <ul className="mt-2 flex flex-wrap gap-1.5">
                {dedotti.map((codice) => (
                  <li
                    key={codice}
                    className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700 ring-1 ring-inset ring-slate-200"
                  >
                    <Building2 className="size-3 text-slate-400" aria-hidden />
                    {etichettaTipo(codice)}
                    <span className="sr-only"> (dal Registro Imprese, non modificabile)</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-sm text-slate-500">
                Nessun dato: importa i dati ufficiali dell'azienda per ricavare dimensione e
                qualifiche.
              </p>
            )}
          </div>
          {vocabolario.data && (
            <GruppoCheckbox
              legenda="Dichiarati da te"
              nota={`Solo se l'azienda lo è davvero: le altre aziende li vedono come «dichiarati». Al massimo ${MAX_TIPI_SOGGETTO}.`}
              opzioni={tipiDichiarabili}
              scelti={valore.tipi_soggetto}
              massimo={MAX_TIPI_SOGGETTO}
              onToggle={(codice) =>
                set(
                  "tipi_soggetto",
                  alterna(
                    valore.tipi_soggetto,
                    codice,
                    tipiDichiarabili.map((t) => t.codice),
                  ),
                )
              }
            />
          )}
        </Sezione>

        <Sezione titolo="Come vuoi partecipare">
          <GruppoCheckbox
            legenda="Ruoli"
            nota="Almeno uno."
            opzioni={RUOLI}
            scelti={valore.ruoli_disponibili}
            minimo={1}
            onToggle={(codice) =>
              set(
                "ruoli_disponibili",
                alterna(
                  valore.ruoli_disponibili,
                  codice,
                  RUOLI.map((r) => r.codice),
                ),
              )
            }
          />
          {vocabolario.data && (
            <GruppoCheckbox
              legenda="Forme di aggregazione che accetti"
              opzioni={forme}
              scelti={valore.forme_accettate}
              onToggle={(codice) =>
                set(
                  "forme_accettate",
                  alterna(
                    valore.forme_accettate,
                    codice,
                    forme.map((f) => f.codice),
                  ),
                )
              }
            />
          )}
          <label className="flex cursor-pointer items-start gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              className="mt-0.5 size-4 shrink-0 cursor-pointer accent-brand-500 disabled:cursor-not-allowed"
              checked={valore.accetta_inviti}
              onChange={(e) => set("accetta_inviti", e.target.checked)}
            />
            <span>
              <span className="font-medium">Accetto inviti dalle altre aziende</span>
              <span className="block text-xs text-slate-500">
                Se lo togli, le altre aziende non possono invitarti ai loro partenariati.
              </span>
            </span>
          </label>
        </Sezione>

        <Sezione titolo="Dove e su cosa" descrizione="Aiuta a proporti i partenariati giusti.">
          <SceltaLookup
            etichetta="Regioni d'interesse"
            opzioni={lookups?.regioni}
            scelti={valore.regioni_interesse}
            onChange={(v) => set("regioni_interesse", v)}
            massimo={MAX_REGIONI}
          />
          <SceltaPaesi
            scelti={valore.paesi_interesse}
            onChange={(v) => set("paesi_interesse", v)}
          />
          <SceltaLookup
            etichetta="Settori d'interesse"
            opzioni={lookups?.settori}
            scelti={valore.settori_interesse}
            onChange={(v) => set("settori_interesse", v)}
            massimo={MAX_SETTORI}
          />
          <SceltaLookup
            etichetta="Tipologie di bando che non ti interessano"
            aiuto="Non ti proporremo partenariati su queste tipologie."
            opzioni={lookups?.tipologie_bando}
            scelti={valore.categorie_bando_escluse}
            onChange={(v) => set("categorie_bando_escluse", v)}
            massimo={MAX_CATEGORIE_ESCLUSE}
          />
        </Sezione>

        <Sezione
          titolo="Esperienze"
          descrizione={
            anonimo
              ? "Finché resti anonima, le altre aziende vedono solo il programma (senza anno, ruolo e titolo)."
              : "Progetti finanziati a cui l'azienda ha partecipato."
          }
        >
          <EsperienzeEditor
            valore={valore.esperienze}
            onChange={(v) => set("esperienze", v)}
            programmi={lookups?.programmi ?? []}
            mostraErrori={mostraErrori}
          />
        </Sezione>

        <Sezione
          titolo="Certificazioni e infrastrutture"
          descrizione={
            anonimo
              ? "Finché resti anonima, le certificazioni si vedono solo per categoria e le infrastrutture non si vedono."
              : undefined
          }
        >
          <VociLibere
            etichetta="Certificazioni"
            voci={valore.certificazioni}
            onChange={(v) => set("certificazioni", v)}
            massimo={MAX_CERTIFICAZIONI}
            lunghezzaMassima={MAX_CERTIFICAZIONE}
            segnaposto="Es. ISO 9001:2015"
          />
          <TestoConContatore
            etichetta="Infrastrutture"
            aiuto="Laboratori, impianti, attrezzature che metti a disposizione di un progetto."
            valore={valore.infrastrutture}
            onChange={(v) => set("infrastrutture", v)}
            righe={3}
          />
        </Sezione>
      </fieldset>

      {/* Barra di salvataggio: compare solo con modifiche, esito o errore. */}
      {barraVisibile && (
        <div className="fixed inset-x-0 bottom-0 z-40 border-t border-slate-200 bg-white/95 backdrop-blur">
          <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
            <div className="text-sm" role="status" aria-live="polite">
              {erroreBarra ? (
                <span className="text-red-600" role="alert">
                  {erroreBarra}
                </span>
              ) : dirty ? (
                <span className="font-medium text-slate-700">{PARTNER_COPY.modificheNonSalvate}</span>
              ) : (
                <span className="font-medium text-emerald-600">{PARTNER_COPY.salvato}</span>
              )}
            </div>
            {dirty && (
              <div className="flex gap-2">
                <Button variant="ghost" onClick={annulla} disabled={salva.isPending}>
                  {PARTNER_COPY.annulla}
                </Button>
                <Button onClick={() => void handleSalva()} loading={salva.isPending}>
                  {PARTNER_COPY.salva}
                </Button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
