import { useId, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { useLookups } from "../../hooks/useLookups";
import { usePartenariatiVocabolario } from "../../hooks/usePartenariatiVocabolario";
import { useSalvaPartnerProfile } from "../../hooks/usePartnerProfile";
import { apiErrorMessage } from "../../lib/api";
import { cn } from "../../lib/cn";
import { PARTNER_COPY } from "../../lib/copy";
import type {
  FormaProfiloPartner,
  LookupItem,
  PartnerProfileInput,
  RuoloPartner,
  TipoSoggettoPartenariato,
} from "../../types";
import { Alert } from "../ui/Alert";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Checkbox } from "../ui/Checkbox";
import { Chip } from "../ui/Chip";
import { TextField } from "../ui/Field";
import { InlineError } from "../ui/InlineError";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { TagSelect } from "../ui/TagSelect";
import { Skeleton } from "../ui/states";
import { SceltaPaesi, TestoLungo } from "./CampiCall";
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
export function alterna<T extends string>(scelti: T[], codice: T, ordine: readonly T[]): T[] {
  const insieme = new Set(scelti);
  if (insieme.has(codice)) insieme.delete(codice);
  else insieme.add(codice);
  const noti = ordine.filter((c) => insieme.has(c));
  const altri = scelti.filter((c) => insieme.has(c) && !ordine.includes(c));
  return [...noti, ...altri];
}

// ---- Blocchi del form ---------------------------------------------------------

/** Sezione del form: titolo con il filetto (livello 3, dentro la sezione
 *  «Profilo partner»), una riga di descrizione e i campi. */
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
    <Section aria-labelledby={id}>
      <div className="flex flex-col gap-2">
        <SectionHeader id={id} titolo={titolo} livello={3} />
        {descrizione && <p className="text-small text-ink-3">{descrizione}</p>}
      </div>
      <div className="flex flex-col gap-6">{children}</div>
    </Section>
  );
}

export interface OpzioneCheckbox<T extends string> {
  codice: T;
  etichetta: string;
  nota?: string;
}

/** Gruppo di checkbox con legenda: al limite le voci non scelte si
 *  disabilitano (quelle scelte restano togliibili). */
export function GruppoCheckbox<T extends string>({
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
    <fieldset className="flex min-w-0 flex-col gap-2" aria-describedby={nota ? idNota : undefined}>
      <legend className="mb-1 text-small font-medium text-ink">{legenda}</legend>
      {nota && (
        <p id={idNota} className="text-small text-ink-3">
          {nota}
        </p>
      )}
      <div className={cn("grid gap-x-4 gap-y-2", colonne === 2 && "sm:grid-cols-2")}>
        {opzioni.map((o) => {
          const scelto = scelti.includes(o.codice);
          const bloccato = (!scelto && pieno) || (scelto && scelti.length <= minimo);
          return (
            <Checkbox
              key={o.codice}
              label={o.etichetta}
              descrizione={o.nota}
              checked={scelto}
              disabled={bloccato}
              onChange={() => onToggle(o.codice)}
            />
          );
        })}
      </div>
    </fieldset>
  );
}

/** Voci scelte come `Chip` rimovibili, sotto il campo che le aggiunge. */
function VociScelte({
  voci,
  onRimuovi,
}: {
  voci: Array<{ chiave: string; testo: string }>;
  onRimuovi: (chiave: string) => void;
}) {
  if (voci.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-1.5">
      {voci.map((v) => (
        <li key={v.chiave} className="max-w-full">
          <Chip onRemove={() => onRimuovi(v.chiave)} label={`Rimuovi «${v.testo}»`}>
            {v.testo}
          </Chip>
        </li>
      ))}
    </ul>
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
    <div className="flex flex-col gap-1.5">
      <div className="flex max-w-md items-end gap-2">
        <div className="min-w-0 flex-1">
          <TextField
            id={id}
            label={etichetta}
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
          />
        </div>
        <Button
          variant="ghost"
          onClick={aggiungi}
          disabled={pieno || !bozza.trim()}
          aria-label={`Aggiungi a ${etichetta.toLowerCase()}`}
        >
          Aggiungi
        </Button>
      </div>
      {aiuto && (
        <p id={`${id}-aiuto`} className="text-small text-ink-3">
          {aiuto}
        </p>
      )}
      {avviso && (
        <p className="text-small text-warning-ink" role="status">
          {avviso}
        </p>
      )}
      <VociScelte
        voci={voci.map((v) => ({ chiave: v, testo: v }))}
        onRimuovi={(v) => onChange(voci.filter((x) => x !== v))}
      />
    </div>
  );
}

/** Scelta multipla su una lookup del catalogo (regioni, settori, tipologie):
 *  ricerca + chip rimovibili. */
export function SceltaLookup({
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
    <div className="flex flex-col gap-1.5">
      <p className="text-small font-medium text-ink">{etichetta}</p>
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
      {aiuto && <p className="text-small text-ink-3">{aiuto}</p>}
      <VociScelte
        voci={scelti.map((id) => ({ chiave: String(id), testo: nomi.get(id) ?? `#${id}` }))}
        onRimuovi={(chiave) => alternaId(Number(chiave))}
      />
    </div>
  );
}

/** Testo lungo facoltativo: vuoto = `null` (come lo vuole il server). */
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
  return (
    <TestoLungo
      etichetta={etichetta}
      aiuto={aiuto}
      valore={valore ?? ""}
      onChange={(v) => onChange(v || null)}
      massimo={MAX_TESTO_LUNGO}
      righe={righe}
    />
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
      <fieldset disabled={!editable} className="flex min-w-0 flex-col gap-8">
        <legend className="sr-only">Profilo partner</legend>

        {anonimo && avvisiAnonimato.length > 0 && (
          <Alert
            tono="attenzione"
            titolo="Nei testi c'è qualcosa che potrebbe far riconoscere l'azienda anche se resta anonima:"
          >
            <ul className="list-disc pl-5">
              {avvisiAnonimato.map((a, i) => (
                <li key={i}>{a}</li>
              ))}
            </ul>
            <p className="mt-1 text-small">Non blocca il salvataggio: valuta tu se toglierlo.</p>
          </Alert>
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
            <Alert tono="errore">
              {apiErrorMessage(vocabolario.error, "Impossibile caricare l'elenco delle competenze.")}
            </Alert>
          ) : (
            <div className="flex flex-col gap-5">
              <p className="text-body text-ink-2">
                Competenze scelte:{" "}
                <span className="font-medium text-ink tabular-nums">
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
          <div className="flex flex-col gap-2">
            <p className="text-small font-medium text-ink">Dal Registro Imprese</p>
            {dedotti.length > 0 ? (
              <ul className="flex flex-wrap gap-1.5">
                {dedotti.map((codice) => (
                  <li key={codice}>
                    <Badge>
                      {etichettaTipo(codice)}
                      <span className="sr-only"> (dal Registro Imprese, non modificabile)</span>
                    </Badge>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-body text-ink-2">
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
          <Checkbox
            label="Accetto inviti dalle altre aziende"
            descrizione="Se lo togli, le altre aziende non possono invitarti ai loro partenariati."
            checked={valore.accetta_inviti}
            onChange={(e) => set("accetta_inviti", e.target.checked)}
          />
        </Sezione>

        <Sezione titolo="Dove e su cosa" descrizione="Aiuta a proporti i partenariati giusti.">
          <SceltaLookup
            etichetta="Regioni d'interesse"
            opzioni={lookups?.regioni}
            scelti={valore.regioni_interesse}
            onChange={(v) => set("regioni_interesse", v)}
            massimo={MAX_REGIONI}
          />
          <div className="flex flex-col gap-1.5">
            <SceltaPaesi
              etichetta="Paesi d'interesse"
              scelti={valore.paesi_interesse}
              onChange={(v) => set("paesi_interesse", v)}
              massimo={MAX_PAESI}
            />
            <p className="text-small text-ink-3">Per i bandi europei e internazionali.</p>
          </div>
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

      {/* Barra di salvataggio: compare solo con modifiche, esito o errore. Su
          desktop parte dal bordo della barra laterale (248px) per non
          coprirne il fondo. */}
      {barraVisibile && (
        <div className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-sheet lg:left-[248px]">
          <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-10">
            <div className="text-body" role="status" aria-live="polite">
              {erroreBarra ? (
                <InlineError>{erroreBarra}</InlineError>
              ) : dirty ? (
                <span className="text-ink-2">{PARTNER_COPY.modificheNonSalvate}</span>
              ) : (
                <span className="font-medium text-fit-ink">{PARTNER_COPY.salvato}</span>
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
