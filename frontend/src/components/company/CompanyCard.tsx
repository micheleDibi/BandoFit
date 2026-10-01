import { useEffect, useState, type FormEvent } from "react";
import { useCompany, useSaveCompany, type CompanyPayload } from "../../hooks/useCompany";
import { useLookups } from "../../hooks/useLookups";
import { apiErrorMessage } from "../../lib/api";
import type { CompanyProfile } from "../../types";
import { Button } from "../ui/Button";
import { Chip } from "../ui/Chip";
import { Combobox } from "../ui/Combobox";
import { DefinitionList, type Definizione } from "../ui/Facts";
import { SelectField, TextField } from "../ui/Field";
import { InlineError } from "../ui/InlineError";
import { Skeleton } from "../ui/states";
import { TagSelect } from "../ui/TagSelect";
import { useToast } from "../ui/Toast";

const CLASSI = [
  { value: "micro", label: "Micro impresa", nota: "meno di 10 dipendenti" },
  { value: "piccola", label: "Piccola impresa", nota: "meno di 50 dipendenti" },
  { value: "media", label: "Media impresa", nota: "meno di 250 dipendenti" },
  { value: "grande", label: "Grande impresa", nota: null },
];

const FASCE = [
  { value: "fino_100k", label: "Fino a 100.000 €" },
  { value: "100k_500k", label: "100.000 – 500.000 €" },
  { value: "500k_2m", label: "500.000 € – 2 mln €" },
  { value: "2m_10m", label: "2 – 10 mln €" },
  { value: "10m_50m", label: "10 – 50 mln €" },
  { value: "oltre_50m", label: "Oltre 50 mln €" },
];

/** Il modulo dei dati aziendali; la pagina ne tiene la bozza durante la modifica. */
export interface FormState {
  ragione_sociale: string;
  forma_giuridica: string;
  partita_iva: string;
  codice_fiscale: string;
  ateco_id: number | null;
  settore_id: number | null;
  regione_id: number | null;
  beneficiari_ids: number[];
  anno_fondazione: string;
  indirizzo: string;
  comune: string;
  provincia: string;
  cap: string;
  classe_dimensionale: string;
  numero_dipendenti: string;
  fascia_fatturato: string;
  pec: string;
  telefono: string;
  sito_web: string;
}

const EMPTY: FormState = {
  ragione_sociale: "",
  forma_giuridica: "",
  partita_iva: "",
  codice_fiscale: "",
  ateco_id: null,
  settore_id: null,
  regione_id: null,
  beneficiari_ids: [],
  anno_fondazione: "",
  indirizzo: "",
  comune: "",
  provincia: "",
  cap: "",
  classe_dimensionale: "",
  numero_dipendenti: "",
  fascia_fatturato: "",
  pec: "",
  telefono: "",
  sito_web: "",
};

function toFormState(company: CompanyProfile | null): FormState {
  if (!company) return EMPTY;
  return {
    ragione_sociale: company.ragione_sociale ?? "",
    forma_giuridica: company.forma_giuridica ?? "",
    partita_iva: company.partita_iva ?? "",
    codice_fiscale: company.codice_fiscale ?? "",
    ateco_id: company.ateco_id,
    settore_id: company.settore_id,
    regione_id: company.regione_id,
    beneficiari_ids: company.beneficiari_ids ?? [],
    anno_fondazione: company.anno_fondazione ? String(company.anno_fondazione) : "",
    indirizzo: company.indirizzo ?? "",
    comune: company.comune ?? "",
    provincia: company.provincia ?? "",
    cap: company.cap ?? "",
    classe_dimensionale: company.classe_dimensionale ?? "",
    numero_dipendenti:
      company.numero_dipendenti !== null ? String(company.numero_dipendenti) : "",
    fascia_fatturato: company.fascia_fatturato ?? "",
    pec: company.pec ?? "",
    telefono: company.telefono ?? "",
    sito_web: company.sito_web ?? "",
  };
}

function validate(form: FormState): string | null {
  if (!form.ragione_sociale.trim()) return "La ragione sociale è obbligatoria.";
  const piva = form.partita_iva.trim().toUpperCase().replace(/^IT/, "").replace(/\s/g, "");
  if (!/^\d{11}$/.test(piva)) return "La partita IVA deve essere composta da 11 cifre.";
  if (form.cap && !/^\d{5}$/.test(form.cap.trim())) return "Il CAP deve avere 5 cifre.";
  if (form.pec && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.pec.trim()))
    return "La PEC non è un indirizzo email valido.";
  return null;
}

function toPayload(form: FormState): CompanyPayload {
  const opt = (v: string) => (v.trim() === "" ? null : v.trim());
  return {
    ragione_sociale: form.ragione_sociale.trim(),
    forma_giuridica: opt(form.forma_giuridica),
    partita_iva: form.partita_iva.trim(),
    codice_fiscale: opt(form.codice_fiscale),
    ateco_id: form.ateco_id,
    settore_id: form.settore_id,
    regione_id: form.regione_id,
    beneficiari_ids: form.beneficiari_ids,
    anno_fondazione: form.anno_fondazione ? Number(form.anno_fondazione) : null,
    indirizzo: opt(form.indirizzo),
    comune: opt(form.comune),
    provincia: opt(form.provincia),
    cap: opt(form.cap),
    classe_dimensionale: opt(form.classe_dimensionale),
    numero_dipendenti: form.numero_dipendenti ? Number(form.numero_dipendenti) : null,
    fascia_fatturato: opt(form.fascia_fatturato),
    pec: opt(form.pec),
    telefono: opt(form.telefono),
    sito_web: opt(form.sito_web),
  };
}

/** Riepilogo in sola lettura (tavola Azienda): etichetta a sinistra, valore a
 *  destra; solo i campi compilati. */
function CompanySummary({ company }: { company: CompanyProfile }) {
  const classe = CLASSI.find((c) => c.value === company.classe_dimensionale);
  const voci: (Definizione | null)[] = [
    { etichetta: "Ragione sociale", valore: company.ragione_sociale },
    company.forma_giuridica ? { etichetta: "Forma giuridica", valore: company.forma_giuridica } : null,
    { etichetta: "Partita IVA", valore: company.partita_iva },
    company.codice_fiscale ? { etichetta: "Codice fiscale", valore: company.codice_fiscale } : null,
    company.ateco_codice
      ? {
          etichetta: "Codice ATECO",
          valore: company.ateco_codice,
          nota: company.ateco_descrizione ?? undefined,
        }
      : null,
    company.settore_nome ? { etichetta: "Settore", valore: company.settore_nome } : null,
    company.regione_nome ? { etichetta: "Regione", valore: company.regione_nome } : null,
    company.beneficiari?.length
      ? {
          etichetta: "Categorie di beneficiario",
          valore: company.beneficiari.map((b) => b.nome).join(", "),
        }
      : null,
    company.anno_fondazione
      ? { etichetta: "Anno di fondazione", valore: String(company.anno_fondazione) }
      : null,
    [company.indirizzo, company.cap, company.comune, company.provincia].some(Boolean)
      ? {
          etichetta: "Sede legale",
          valore: [company.indirizzo, company.cap, company.comune, company.provincia]
            .filter(Boolean)
            .join(", "),
        }
      : null,
    classe ? { etichetta: "Dimensione", valore: classe.label, nota: classe.nota ?? undefined } : null,
    company.numero_dipendenti !== null
      ? { etichetta: "Numero dipendenti", valore: String(company.numero_dipendenti) }
      : null,
    company.fascia_fatturato
      ? {
          etichetta: "Fascia di fatturato",
          valore: FASCE.find((f) => f.value === company.fascia_fatturato)?.label ?? null,
        }
      : null,
    company.pec ? { etichetta: "PEC", valore: company.pec } : null,
    company.telefono ? { etichetta: "Telefono", valore: company.telefono } : null,
    company.sito_web ? { etichetta: "Sito web", valore: company.sito_web } : null,
  ];
  return <DefinitionList items={voci.filter((v): v is Definizione => !!v && !!v.valore)} />;
}

/** Dati aziendali: il riepilogo e, in modifica, il modulo. Lo stato di
 *  modifica è della pagina (il pulsante «Modifica» sta nell'intestazione),
 *  e così la bozza del modulo (`bozza`/`onBozzaChange`): la scheda si smonta
 *  cambiando scheda, la bozza no. Stesse validazioni e stesso payload di
 *  sempre. Alla prima compilazione (nessun dato) si parte già in modifica. */
export function CompanyCard({
  editing,
  onEditingChange,
  bozza = null,
  onBozzaChange,
}: {
  editing: boolean;
  onEditingChange: (editing: boolean) => void;
  /** Bozza della modifica in corso, conservata dalla pagina. */
  bozza?: FormState | null;
  onBozzaChange?: (bozza: FormState | null) => void;
}) {
  const { data, isPending } = useCompany();
  const { data: lookups } = useLookups();
  const saveCompany = useSaveCompany();
  const { mostra } = useToast();

  // Il modulo riparte dalla bozza della pagina (modifica in corso, scheda
  // rimontata dopo un cambio di scheda) o dai dati in cache: mai vuoto in
  // modifica, che al salvataggio azzererebbe i campi.
  const [form, setForm] = useState<FormState>(() =>
    editing && bozza ? bozza : data ? toFormState(data.company) : EMPTY,
  );
  const [inizializzato, setInizializzato] = useState(() => (editing && !!bozza) || !!data);
  const [validationError, setValidationError] = useState<string | null>(null);

  useEffect(() => {
    // Mai risincronizzare il form MENTRE si sta modificando: un refetch in
    // background (es. al refocus della finestra) cancellerebbe ciò che
    // l'utente sta scrivendo. L'eccezione è il primo arrivo dei dati, anche
    // in modifica: prima di allora il modulo non è dell'azienda.
    if (data && (!editing || !inizializzato)) {
      setForm(toFormState(data.company));
      setInizializzato(true);
      // Senza alcun dato non c'è nulla da riepilogare: si parte dal modulo.
      if (!editing && data.editable && !data.company) onEditingChange(true);
    }
  }, [data, editing, inizializzato, onEditingChange]);

  // La bozza alla pagina: le modifiche sopravvivono al cambio di scheda; fuori
  // dalla modifica non c'è bozza.
  useEffect(() => {
    if (!onBozzaChange) return;
    onBozzaChange(editing && inizializzato ? form : null);
  }, [editing, inizializzato, form, onBozzaChange]);

  if (isPending || (editing && !inizializzato)) {
    return (
      <div className="flex flex-col gap-3" aria-hidden>
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (!data) return null;

  const company = data.company;

  // Vista del membro: solo riepilogo.
  if (!data.editable) {
    return (
      <div className="flex flex-col gap-4">
        <p className="text-small text-ink-3">
          Dati della tua azienda, gestiti dal titolare (sola lettura).
        </p>
        {company ? (
          <CompanySummary company={company} />
        ) : (
          <p className="text-body text-ink-2">Il titolare non ha ancora compilato i dati aziendali.</p>
        )}
      </div>
    );
  }

  const set =
    (key: keyof FormState) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
      setForm((f) => ({ ...f, [key]: e.target.value }));

  const beneficiariOptions = (lookups?.beneficiari ?? []).map((b) => ({ id: b.id, label: b.nome }));
  // Ripiego all'id: se il catalogo non è ancora arrivato il chip resta leggibile.
  const beneficiarioNome = (id: number) =>
    beneficiariOptions.find((b) => b.id === id)?.label ?? String(id);
  const toggleBeneficiario = (id: number) =>
    setForm((f) => ({
      ...f,
      beneficiari_ids: f.beneficiari_ids.includes(id)
        ? f.beneficiari_ids.filter((x) => x !== id)
        : [...f.beneficiari_ids, id],
    }));

  const handleCancel = () => {
    setForm(toFormState(company));
    setValidationError(null);
    onEditingChange(false);
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    // Mai un PUT con i campi non ancora caricati.
    if (!inizializzato) return;
    const problem = validate(form);
    setValidationError(problem);
    if (problem) return;
    try {
      await saveCompany.mutateAsync(toPayload(form));
      onEditingChange(false);
      mostra({ testo: "Dati aziendali salvati" });
    } catch {
      // errore mostrato sotto
    }
  };

  if (!editing) {
    return company ? (
      <CompanySummary company={company} />
    ) : (
      <p className="text-body text-ink-2">Nessun dato inserito.</p>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Ragione sociale"
          required
          value={form.ragione_sociale}
          onChange={set("ragione_sociale")}
        />
        <TextField
          label="Forma giuridica"
          placeholder="es. SRL, SPA, ditta individuale"
          value={form.forma_giuridica}
          onChange={set("forma_giuridica")}
        />
        <TextField
          label="Partita IVA"
          required
          inputMode="numeric"
          placeholder="11 cifre"
          value={form.partita_iva}
          onChange={set("partita_iva")}
        />
        <TextField label="Codice fiscale" value={form.codice_fiscale} onChange={set("codice_fiscale")} />
        <Combobox
          label="Codice ATECO primario"
          options={(lookups?.codici_ateco ?? []).map((a) => ({
            id: a.id,
            label: a.codice,
            sublabel: a.descrizione ?? undefined,
          }))}
          value={form.ateco_id}
          onChange={(id) => setForm((f) => ({ ...f, ateco_id: id }))}
        />
        <Combobox
          label="Settore"
          options={(lookups?.settori ?? []).map((s) => ({ id: s.id, label: s.nome }))}
          value={form.settore_id}
          onChange={(id) => setForm((f) => ({ ...f, settore_id: id }))}
        />
        <Combobox
          label="Regione"
          options={(lookups?.regioni ?? []).map((r) => ({ id: r.id, label: r.nome }))}
          value={form.regione_id}
          onChange={(id) => setForm((f) => ({ ...f, regione_id: id }))}
        />
        <TextField
          label="Anno di fondazione"
          type="number"
          min={1800}
          max={2100}
          value={form.anno_fondazione}
          onChange={set("anno_fondazione")}
        />
        {/* Dichiarato, non deducibile dalla visura: il catalogo distingue
            Istituti Scolastici, Enti pubblici, Organismi di formazione… che
            nessun attributo camerale esprime. Multi-valore.
            TagSelect è solo il selettore (la sua `label` è sr-only e non
            mostra i valori scelti): etichetta e chip stanno qui. */}
        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <span className="text-small font-medium text-ink">Categorie di beneficiario</span>
          {form.beneficiari_ids.length > 0 && (
            <ul className="flex flex-wrap gap-1.5">
              {form.beneficiari_ids.map((id) => (
                <li key={id}>
                  <Chip
                    onRemove={() => toggleBeneficiario(id)}
                    label={`Rimuovi ${beneficiarioNome(id)}`}
                  >
                    {beneficiarioNome(id)}
                  </Chip>
                </li>
              ))}
            </ul>
          )}
          <TagSelect
            label="Aggiungi una categoria di beneficiario"
            options={beneficiariOptions}
            values={form.beneficiari_ids}
            onToggle={toggleBeneficiario}
            placeholder="Cerca e aggiungi una categoria…"
          />
          <p className="text-small text-ink-3">
            Come ti presenti ai bandi: PMI, Startup, Organismo di formazione, Ente pubblico… Puoi
            sceglierne più di una. Finché è vuota, i bandi che limitano i beneficiari non la
            conteggiano nella compatibilità.
          </p>
        </div>
      </div>

      <fieldset className="grid gap-4 border-t border-line pt-4 sm:grid-cols-2">
        <legend className="sr-only">Sede legale</legend>
        <div className="sm:col-span-2">
          <TextField label="Indirizzo sede legale" value={form.indirizzo} onChange={set("indirizzo")} />
        </div>
        <TextField label="Comune" value={form.comune} onChange={set("comune")} />
        <div className="grid grid-cols-2 gap-4">
          <TextField label="Provincia" value={form.provincia} onChange={set("provincia")} />
          <TextField label="CAP" inputMode="numeric" value={form.cap} onChange={set("cap")} />
        </div>
      </fieldset>

      <fieldset className="grid gap-4 border-t border-line pt-4 sm:grid-cols-3">
        <legend className="sr-only">Dimensione aziendale</legend>
        <SelectField
          label="Classe dimensionale"
          value={form.classe_dimensionale}
          onChange={set("classe_dimensionale")}
        >
          <option value="">Non specificata</option>
          {CLASSI.map((c) => (
            <option key={c.value} value={c.value}>
              {c.nota ? `${c.label} (${c.nota})` : c.label}
            </option>
          ))}
        </SelectField>
        <TextField
          label="Numero dipendenti"
          type="number"
          min={0}
          value={form.numero_dipendenti}
          onChange={set("numero_dipendenti")}
        />
        <SelectField
          label="Fascia di fatturato"
          value={form.fascia_fatturato}
          onChange={set("fascia_fatturato")}
        >
          <option value="">Non specificata</option>
          {FASCE.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </SelectField>
      </fieldset>

      <fieldset className="grid gap-4 border-t border-line pt-4 sm:grid-cols-3">
        <legend className="sr-only">Contatti</legend>
        <TextField label="PEC" type="email" value={form.pec} onChange={set("pec")} />
        <TextField label="Telefono" type="tel" value={form.telefono} onChange={set("telefono")} />
        <TextField
          label="Sito web"
          placeholder="https://…"
          value={form.sito_web}
          onChange={set("sito_web")}
        />
      </fieldset>

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" loading={saveCompany.isPending}>
          Salva i dati aziendali
        </Button>
        <Button type="button" variant="ghost" onClick={handleCancel}>
          Annulla
        </Button>
        {(validationError || saveCompany.isError) && (
          <InlineError>{validationError ?? apiErrorMessage(saveCompany.error)}</InlineError>
        )}
      </div>
    </form>
  );
}
