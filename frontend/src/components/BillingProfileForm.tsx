import { BadgeCheck, ShieldCheck, Wand2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useBillingPrefill, useSaveBillingProfile } from "../hooks/useBillingProfile";
import { apiErrorMessage } from "../lib/api";
import { cn } from "../lib/cn";
import { formatDate } from "../lib/format";
import { isValidPartitaIva, normalizePartitaIva } from "../lib/partitaIva";
import type { BillingProfile, BillingProfileInput, TipoSoggetto } from "../types";
import { Button } from "./ui/Button";
import { SelectField, TextField } from "./ui/Field";

/** Paesi ammessi per «azienda_ue» (stessa lista del backend, schemas/billing.py):
 *  ISO 3166-1 alpha-2, senza l'Italia. Ordinati per nome italiano. */
const PAESI_UE: Array<[string, string]> = [
  ["AT", "Austria"],
  ["BE", "Belgio"],
  ["BG", "Bulgaria"],
  ["CY", "Cipro"],
  ["HR", "Croazia"],
  ["DK", "Danimarca"],
  ["EE", "Estonia"],
  ["FI", "Finlandia"],
  ["FR", "Francia"],
  ["DE", "Germania"],
  ["GR", "Grecia"],
  ["IE", "Irlanda"],
  ["LV", "Lettonia"],
  ["LT", "Lituania"],
  ["LU", "Lussemburgo"],
  ["MT", "Malta"],
  ["NL", "Paesi Bassi"],
  ["PL", "Polonia"],
  ["PT", "Portogallo"],
  ["CZ", "Repubblica Ceca"],
  ["RO", "Romania"],
  ["SK", "Slovacchia"],
  ["SI", "Slovenia"],
  ["ES", "Spagna"],
  ["SE", "Svezia"],
  ["HU", "Ungheria"],
];

const TIPI: Array<{ value: TipoSoggetto; label: string; hint: string }> = [
  { value: "azienda_it", label: "Azienda italiana", hint: "Fattura elettronica via SDI" },
  { value: "privato_it", label: "Privato", hint: "Fattura col codice fiscale" },
  { value: "azienda_ue", label: "Azienda UE", hint: "Reverse charge, senza IVA" },
];

interface FormState {
  tipo_soggetto: TipoSoggetto;
  denominazione: string;
  nome: string;
  cognome: string;
  partita_iva: string;
  codice_fiscale: string;
  paese: string;
  indirizzo: string;
  comune: string;
  provincia: string;
  cap: string;
  codice_destinatario: string;
  pec: string;
}

type FieldErrors = Partial<Record<keyof FormState, string>>;

const EMPTY_FORM: FormState = {
  tipo_soggetto: "azienda_it",
  denominazione: "",
  nome: "",
  cognome: "",
  partita_iva: "",
  codice_fiscale: "",
  paese: "IT",
  indirizzo: "",
  comune: "",
  provincia: "",
  cap: "",
  codice_destinatario: "",
  pec: "",
};

const fromProfile = (p: BillingProfile): FormState => ({
  tipo_soggetto: p.tipo_soggetto,
  denominazione: p.denominazione ?? "",
  nome: p.nome ?? "",
  cognome: p.cognome ?? "",
  partita_iva: p.partita_iva ?? "",
  codice_fiscale: p.codice_fiscale ?? "",
  paese: p.paese,
  indirizzo: p.indirizzo,
  comune: p.comune,
  provincia: p.provincia ?? "",
  cap: p.cap,
  codice_destinatario: p.codice_destinatario ?? "",
  pec: p.pec ?? "",
});

/** Validazione locale, per tipo di soggetto. Duplica le regole di forma di
 *  schemas/billing.py: il backend resta l'autorità, ma il suo 422 è generico
 *  e senza questo controllo l'utente non saprebbe quale campo correggere. */
function validate(f: FormState): FieldErrors {
  const e: FieldErrors = {};
  if (!f.indirizzo.trim()) e.indirizzo = "Inserisci l'indirizzo.";
  if (!f.comune.trim()) e.comune = "Inserisci il comune.";
  if (!f.cap.trim()) e.cap = "Inserisci il CAP.";

  if (f.tipo_soggetto !== "azienda_ue") {
    if (f.cap.trim() && !/^\d{5}$/.test(f.cap.trim()))
      e.cap = "Il CAP italiano è di 5 cifre.";
    if (!f.provincia.trim()) e.provincia = "Inserisci la provincia.";
    else if (!/^[A-Za-z]{2}$/.test(f.provincia.trim()))
      e.provincia = "Usa la sigla di 2 lettere (es. MI).";
  }

  if (f.tipo_soggetto === "azienda_it") {
    if (!f.denominazione.trim()) e.denominazione = "Inserisci la ragione sociale.";
    if (!isValidPartitaIva(normalizePartitaIva(f.partita_iva)))
      e.partita_iva = "La partita IVA non è valida: verifica le 11 cifre.";
    const sdi = f.codice_destinatario.trim();
    if (sdi && !/^[A-Za-z0-9]{7}$/.test(sdi))
      e.codice_destinatario = "Il codice destinatario SDI è di 7 caratteri.";
    if (!sdi && !f.pec.trim())
      e.codice_destinatario = "Serve il codice destinatario SDI oppure la PEC.";
    if (f.pec.trim() && !f.pec.includes("@")) e.pec = "Inserisci una PEC valida.";
  } else if (f.tipo_soggetto === "privato_it") {
    if (!f.nome.trim()) e.nome = "Inserisci il nome.";
    if (!f.cognome.trim()) e.cognome = "Inserisci il cognome.";
    if (!/^[A-Za-z0-9]{16}$/.test(f.codice_fiscale.trim()))
      e.codice_fiscale = "Il codice fiscale è di 16 caratteri.";
  } else {
    if (!f.paese) e.paese = "Seleziona il paese.";
    if (!f.denominazione.trim()) e.denominazione = "Inserisci la ragione sociale.";
    if (f.partita_iva.trim().length < 4)
      e.partita_iva = "Inserisci la partita IVA del tuo paese.";
  }
  return e;
}

/** Corpo del PUT: solo i campi pertinenti al tipo (gli altri restano assenti,
 *  come si aspetta la validazione del backend). */
function toInput(f: FormState): BillingProfileInput {
  const base = {
    tipo_soggetto: f.tipo_soggetto,
    indirizzo: f.indirizzo.trim(),
    comune: f.comune.trim(),
    cap: f.cap.trim(),
  };
  if (f.tipo_soggetto === "azienda_it") {
    return {
      ...base,
      paese: "IT",
      denominazione: f.denominazione.trim(),
      partita_iva: normalizePartitaIva(f.partita_iva),
      provincia: f.provincia.trim().toUpperCase(),
      codice_destinatario: f.codice_destinatario.trim().toUpperCase() || null,
      pec: f.pec.trim() || null,
    };
  }
  if (f.tipo_soggetto === "privato_it") {
    return {
      ...base,
      paese: "IT",
      nome: f.nome.trim(),
      cognome: f.cognome.trim(),
      codice_fiscale: f.codice_fiscale.trim().toUpperCase(),
      provincia: f.provincia.trim().toUpperCase(),
    };
  }
  return {
    ...base,
    paese: f.paese,
    denominazione: f.denominazione.trim(),
    partita_iva: f.partita_iva.trim(),
  };
}

export interface BillingProfileFormProps {
  /** Anagrafica corrente (null = mai compilata: il form parte vuoto). */
  profile: BillingProfile | null;
  /** Notifica il salvataggio riuscito (riuso nel checkout). */
  onSaved?: () => void;
}

export function BillingProfileForm({ profile, onSaved }: BillingProfileFormProps) {
  const save = useSaveBillingProfile();
  // Il prefill si chiede solo ad anagrafica mai compilata: se esiste già,
  // si parte dai dati salvati.
  const { data: prefill } = useBillingPrefill(profile === null);
  const [form, setForm] = useState<FormState>(() =>
    profile ? fromProfile(profile) : EMPTY_FORM,
  );
  const [errors, setErrors] = useState<FieldErrors>({});
  const [savedFlash, setSavedFlash] = useState(false);

  const set = (key: keyof FormState) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  const prefillHasData =
    !!prefill && Object.values(prefill).some((v) => v !== null && v !== "");

  const applyPrefill = () => {
    if (!prefill) return;
    setErrors({});
    setForm((f) => ({
      ...f,
      tipo_soggetto: prefill.tipo_soggetto ?? f.tipo_soggetto,
      denominazione: prefill.denominazione ?? f.denominazione,
      partita_iva: prefill.partita_iva ?? f.partita_iva,
      codice_fiscale: prefill.codice_fiscale ?? f.codice_fiscale,
      indirizzo: prefill.indirizzo ?? f.indirizzo,
      comune: prefill.comune ?? f.comune,
      provincia: prefill.provincia ?? f.provincia,
      cap: prefill.cap ?? f.cap,
      pec: prefill.pec ?? f.pec,
    }));
  };

  const setTipo = (tipo: TipoSoggetto) => {
    setErrors({});
    setForm((f) => ({
      ...f,
      tipo_soggetto: tipo,
      // Il paese segue il tipo: IT per i soggetti italiani, da scegliere per l'UE.
      paese: tipo === "azienda_ue" ? (f.paese === "IT" ? "" : f.paese) : "IT",
    }));
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSavedFlash(false);
    const errs = validate(form);
    setErrors(errs);
    if (Object.keys(errs).length > 0) return;
    try {
      await save.mutateAsync(toInput(form));
      setSavedFlash(true);
      setTimeout(() => setSavedFlash(false), 3000);
      onSaved?.();
    } catch {
      // errore mostrato sotto il bottone
    }
  };

  const tipo = form.tipo_soggetto;
  const isAzienda = tipo !== "privato_it";

  return (
    <form onSubmit={handleSubmit} noValidate>
      {profile === null && prefillHasData && (
        <div className="mb-4">
          <Button type="button" variant="secondary" size="sm" onClick={applyPrefill}>
            <Wand2 className="size-4" aria-hidden />
            Precompila dai dati della tua azienda
          </Button>
        </div>
      )}

      {/* Tipo di soggetto: decide i campi mostrati e le regole di validazione */}
      <fieldset>
        <legend className="text-sm font-medium text-slate-700">A chi va intestata la fattura?</legend>
        <div className="mt-1.5 grid gap-2 sm:grid-cols-3">
          {TIPI.map((t) => {
            const active = tipo === t.value;
            return (
              <button
                key={t.value}
                type="button"
                aria-pressed={active}
                onClick={() => setTipo(t.value)}
                className={cn(
                  "cursor-pointer rounded-lg border px-3 py-2.5 text-left transition-colors duration-150",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500",
                  active
                    ? "border-brand-500 bg-brand-50 ring-1 ring-inset ring-brand-500"
                    : "border-slate-300 bg-white hover:border-brand-400",
                )}
              >
                <span className={cn("block text-sm font-medium", active ? "text-brand-700" : "text-slate-700")}>
                  {t.label}
                </span>
                <span className="mt-0.5 block text-xs text-slate-500">{t.hint}</span>
              </button>
            );
          })}
        </div>
      </fieldset>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        {tipo === "azienda_ue" && (
          <>
            <p className="rounded-lg bg-brand-50 px-3 py-2 text-sm text-brand-700 sm:col-span-2">
              Al salvataggio verifichiamo la partita IVA nel VIES: se risulta valida, la
              fattura sarà emessa in reverse charge, senza IVA italiana.
            </p>
            <SelectField
              label="Paese"
              required
              value={form.paese}
              error={errors.paese}
              onChange={(e) => setForm((f) => ({ ...f, paese: e.target.value }))}
            >
              <option value="" disabled>
                Seleziona il paese…
              </option>
              {PAESI_UE.map(([code, nome]) => (
                <option key={code} value={code}>
                  {nome}
                </option>
              ))}
            </SelectField>
          </>
        )}

        {isAzienda ? (
          <>
            <div className="sm:col-span-2">
              <TextField
                label="Ragione sociale"
                required
                value={form.denominazione}
                error={errors.denominazione}
                onChange={set("denominazione")}
                autoComplete="organization"
                maxLength={200}
              />
            </div>
            <TextField
              label="Partita IVA"
              required
              value={form.partita_iva}
              error={errors.partita_iva}
              onChange={set("partita_iva")}
              autoComplete="off"
              inputMode={tipo === "azienda_it" ? "numeric" : undefined}
              placeholder={tipo === "azienda_it" ? "11 cifre" : undefined}
              maxLength={20}
            />
          </>
        ) : (
          <>
            <TextField
              label="Nome"
              required
              value={form.nome}
              error={errors.nome}
              onChange={set("nome")}
              autoComplete="given-name"
              maxLength={100}
            />
            <TextField
              label="Cognome"
              required
              value={form.cognome}
              error={errors.cognome}
              onChange={set("cognome")}
              autoComplete="family-name"
              maxLength={100}
            />
            <TextField
              label="Codice fiscale"
              required
              value={form.codice_fiscale}
              error={errors.codice_fiscale}
              onChange={(e) =>
                setForm((f) => ({ ...f, codice_fiscale: e.target.value.toUpperCase() }))
              }
              autoComplete="off"
              placeholder="16 caratteri"
              maxLength={16}
            />
          </>
        )}

        <div className="sm:col-span-2">
          <TextField
            label="Indirizzo"
            required
            value={form.indirizzo}
            error={errors.indirizzo}
            onChange={set("indirizzo")}
            autoComplete="street-address"
            placeholder="Via e numero civico"
            maxLength={200}
          />
        </div>
        <TextField
          label="Comune"
          required
          value={form.comune}
          error={errors.comune}
          onChange={set("comune")}
          autoComplete="address-level2"
          maxLength={100}
        />
        <div className="grid grid-cols-2 gap-4">
          {tipo !== "azienda_ue" && (
            <TextField
              label="Provincia"
              required
              value={form.provincia}
              error={errors.provincia}
              onChange={(e) =>
                setForm((f) => ({ ...f, provincia: e.target.value.toUpperCase() }))
              }
              autoComplete="off"
              placeholder="Es. MI"
              maxLength={2}
            />
          )}
          <TextField
            label="CAP"
            required
            value={form.cap}
            error={errors.cap}
            onChange={set("cap")}
            autoComplete="postal-code"
            inputMode={tipo === "azienda_ue" ? undefined : "numeric"}
            maxLength={10}
          />
        </div>

        {tipo === "azienda_it" && (
          <div className="rounded-lg bg-slate-50 p-4 ring-1 ring-inset ring-slate-200 sm:col-span-2">
            <p className="text-sm font-medium text-slate-700">Recapito della fattura elettronica</p>
            <p className="mt-0.5 text-xs text-slate-500">
              Basta uno dei due: il codice destinatario SDI del tuo gestionale oppure la PEC.
            </p>
            <div className="mt-3 grid gap-4 sm:grid-cols-2">
              <TextField
                label="Codice destinatario SDI"
                value={form.codice_destinatario}
                error={errors.codice_destinatario}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    codice_destinatario: e.target.value.toUpperCase(),
                  }))
                }
                autoComplete="off"
                placeholder="7 caratteri"
                maxLength={7}
              />
              <TextField
                label="PEC"
                type="email"
                value={form.pec}
                error={errors.pec}
                onChange={set("pec")}
                autoComplete="off"
                maxLength={200}
              />
            </div>
          </div>
        )}
        {tipo === "privato_it" && (
          <p className="text-xs text-slate-400 sm:col-span-2">
            Niente SDI o PEC: per i privati la fattura viaggia con il codice «0000000».
          </p>
        )}

        <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
          <Button type="submit" loading={save.isPending}>
            Salva i dati
          </Button>
          {save.isPending && tipo === "azienda_ue" && (
            <span className="text-sm text-slate-500" role="status">
              Verifica della partita IVA nel VIES in corso…
            </span>
          )}
          {savedFlash && (
            <span
              className="inline-flex items-center gap-1.5 text-sm font-medium text-emerald-600"
              role="status"
            >
              <BadgeCheck className="size-4" aria-hidden />
              Dati salvati
            </span>
          )}
          {/* P.IVA UE già verificata: la prova del reverse charge è a posto */}
          {!savedFlash &&
            tipo === "azienda_ue" &&
            profile?.tipo_soggetto === "azienda_ue" &&
            profile.vies_valid && (
              <span className="inline-flex items-center gap-1.5 text-sm font-medium text-emerald-600">
                <ShieldCheck className="size-4" aria-hidden />
                P.IVA verificata nel VIES il {formatDate(profile.vies_checked_at)}
              </span>
            )}
        </div>
        {save.isError && (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 sm:col-span-2" role="alert">
            {apiErrorMessage(save.error)}
          </p>
        )}
      </div>
    </form>
  );
}
