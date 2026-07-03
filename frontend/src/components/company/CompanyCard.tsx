import { BadgeCheck, Building2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { useCompany, useSaveCompany, type CompanyPayload } from "../../hooks/useCompany";
import { useLookups } from "../../hooks/useLookups";
import { apiErrorMessage } from "../../lib/api";
import type { CompanyProfile } from "../../types";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Combobox } from "../ui/Combobox";
import { SelectField, TextField } from "../ui/Field";
import { Skeleton } from "../ui/states";

const CLASSI = [
  { value: "micro", label: "Micro impresa (< 10 dipendenti)" },
  { value: "piccola", label: "Piccola impresa (< 50 dipendenti)" },
  { value: "media", label: "Media impresa (< 250 dipendenti)" },
  { value: "grande", label: "Grande impresa" },
];

const FASCE = [
  { value: "fino_100k", label: "Fino a 100.000 €" },
  { value: "100k_500k", label: "100.000 – 500.000 €" },
  { value: "500k_2m", label: "500.000 € – 2 mln €" },
  { value: "2m_10m", label: "2 – 10 mln €" },
  { value: "10m_50m", label: "10 – 50 mln €" },
  { value: "oltre_50m", label: "Oltre 50 mln €" },
];

interface FormState {
  ragione_sociale: string;
  forma_giuridica: string;
  partita_iva: string;
  codice_fiscale: string;
  ateco_id: number | null;
  settore_id: number | null;
  regione_id: number | null;
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

function ReadOnlyRow({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null;
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="mt-0.5 text-sm text-slate-800">{value}</dd>
    </div>
  );
}

export function CompanyCard() {
  const { data, isPending } = useCompany();
  const { data: lookups } = useLookups();
  const saveCompany = useSaveCompany();

  const [form, setForm] = useState<FormState>(EMPTY);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (data) setForm(toFormState(data.company));
  }, [data]);

  if (isPending) {
    return (
      <Card className="p-6">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="mt-4 h-40 w-full" />
      </Card>
    );
  }
  if (!data) return null;

  const set = (key: keyof FormState) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  // Vista figlio: sola lettura.
  if (!data.editable) {
    const company = data.company;
    return (
      <Card className="p-6">
        <h2 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
          <Building2 className="size-4 text-brand-500" aria-hidden />
          Dati aziendali
        </h2>
        <p className="mt-1 text-sm text-slate-500">
          Dati della famiglia, gestiti dal titolare (sola lettura).
        </p>
        {company ? (
          <dl className="mt-4 grid gap-x-6 gap-y-3 sm:grid-cols-2">
            <ReadOnlyRow label="Ragione sociale" value={company.ragione_sociale} />
            <ReadOnlyRow label="Forma giuridica" value={company.forma_giuridica} />
            <ReadOnlyRow label="Partita IVA" value={company.partita_iva} />
            <ReadOnlyRow label="Codice fiscale" value={company.codice_fiscale} />
            <ReadOnlyRow
              label="Codice ATECO"
              value={
                company.ateco_codice
                  ? `${company.ateco_codice} — ${company.ateco_descrizione ?? ""}`
                  : null
              }
            />
            <ReadOnlyRow label="Settore" value={company.settore_nome} />
            <ReadOnlyRow label="Regione" value={company.regione_nome} />
            <ReadOnlyRow
              label="Sede legale"
              value={[company.indirizzo, company.cap, company.comune, company.provincia]
                .filter(Boolean)
                .join(", ") || null}
            />
            <ReadOnlyRow
              label="Dimensione"
              value={CLASSI.find((c) => c.value === company.classe_dimensionale)?.label ?? null}
            />
            <ReadOnlyRow
              label="Fascia di fatturato"
              value={FASCE.find((f) => f.value === company.fascia_fatturato)?.label ?? null}
            />
            <ReadOnlyRow label="PEC" value={company.pec} />
            <ReadOnlyRow label="Telefono" value={company.telefono} />
            <ReadOnlyRow label="Sito web" value={company.sito_web} />
          </dl>
        ) : (
          <p className="mt-4 text-sm text-slate-400">
            Il titolare non ha ancora compilato i dati aziendali.
          </p>
        )}
      </Card>
    );
  }

  // Vista padre: form completo.
  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSaved(false);
    const problem = validate(form);
    setValidationError(problem);
    if (problem) return;
    try {
      await saveCompany.mutateAsync(toPayload(form));
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch {
      // errore mostrato sotto
    }
  };

  return (
    <Card className="p-6">
      <h2 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
        <Building2 className="size-4 text-brand-500" aria-hidden />
        Dati aziendali
      </h2>
      <p className="mt-1 text-sm text-slate-500">
        Servono per i futuri AI-check e vengono condivisi con gli account collegati.
      </p>

      <form onSubmit={handleSubmit} className="mt-5 space-y-5">
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
          <TextField
            label="Codice fiscale"
            value={form.codice_fiscale}
            onChange={set("codice_fiscale")}
          />
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
        </div>

        <fieldset className="grid gap-4 border-t border-slate-100 pt-4 sm:grid-cols-2">
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

        <fieldset className="grid gap-4 border-t border-slate-100 pt-4 sm:grid-cols-3">
          <legend className="sr-only">Dimensione aziendale</legend>
          <SelectField
            label="Classe dimensionale"
            value={form.classe_dimensionale}
            onChange={set("classe_dimensionale")}
          >
            <option value="">Non specificata</option>
            {CLASSI.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
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

        <fieldset className="grid gap-4 border-t border-slate-100 pt-4 sm:grid-cols-3">
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

        <div className="flex items-center gap-3">
          <Button type="submit" loading={saveCompany.isPending}>
            Salva dati aziendali
          </Button>
          {saved && (
            <span
              className="inline-flex items-center gap-1.5 text-sm font-medium text-emerald-600"
              role="status"
            >
              <BadgeCheck className="size-4" aria-hidden />
              Salvato
            </span>
          )}
          {(validationError || saveCompany.isError) && (
            <span className="text-sm text-red-600" role="alert">
              {validationError ?? apiErrorMessage(saveCompany.error)}
            </span>
          )}
        </div>
      </form>
    </Card>
  );
}
