import { BadgeCheck, ShieldCheck, Wand2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useBillingPrefill, useSaveBillingProfile } from "../hooks/useBillingProfile";
import { apiErrorMessage } from "../lib/api";
import { cn } from "../lib/cn";
import { formatDate } from "../lib/format";
import { nomePaese, paesiOrdinati, PAESI_UE, viesApplicabile } from "../lib/paesi";
import { isValidPartitaIva, normalizePartitaIva } from "../lib/partitaIva";
import type { BillingProfile, BillingProfileInput, TipoSoggetto } from "../types";
import { Button } from "./ui/Button";
import { SelectField, TextField } from "./ui/Field";

const TIPI: Array<{ value: TipoSoggetto; label: string; hint: string }> = [
  { value: "azienda", label: "Azienda", hint: "Fattura intestata all'azienda, con partita IVA" },
  { value: "privato", label: "Privato", hint: "Fattura intestata a te" },
];

const PAESI = paesiOrdinati();

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
}

type FieldErrors = Partial<Record<keyof FormState, string>>;

const EMPTY_FORM: FormState = {
  tipo_soggetto: "azienda",
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
});

/** Normalizzazione della P.IVA UE, specchio di schemas/billing.py: maiuscola,
 *  senza spazi/punti, senza il prefisso paese digitato (EL per la Grecia). */
function normalizzaPivaUe(piva: string, paese: string): string {
  const pulita = piva.replace(/[\s.]/g, "").toUpperCase();
  const prefisso = paese === "GR" ? "EL" : paese;
  return pulita.startsWith(prefisso) && pulita.length > prefisso.length
    ? pulita.slice(prefisso.length)
    : pulita;
}

/** Validazione locale, per tipo di soggetto e paese. Duplica le regole di
 *  forma di schemas/billing.py: il backend resta l'autorità, ma il suo 422 è
 *  generico e senza questo controllo l'utente non saprebbe quale campo
 *  correggere. Le regole italiane (checksum P.IVA, CAP a 5 cifre, provincia,
 *  CF) valgono solo con paese IT. */
function validate(f: FormState): FieldErrors {
  const e: FieldErrors = {};
  const isIt = f.paese === "IT";
  if (!f.indirizzo.trim()) e.indirizzo = "Inserisci l'indirizzo.";
  if (!f.comune.trim()) e.comune = "Inserisci il comune.";
  if (!f.cap.trim()) e.cap = "Inserisci il CAP.";

  if (isIt) {
    if (f.cap.trim() && !/^\d{5}$/.test(f.cap.trim()))
      e.cap = "Il CAP italiano è di 5 cifre.";
    if (!f.provincia.trim()) e.provincia = "Inserisci la provincia.";
    else if (!/^[A-Za-z]{2}$/.test(f.provincia.trim()))
      e.provincia = "Usa la sigla di 2 lettere (es. MI).";
  }

  if (f.tipo_soggetto === "azienda") {
    if (!f.denominazione.trim()) e.denominazione = "Inserisci la ragione sociale.";
    if (isIt) {
      if (!isValidPartitaIva(normalizePartitaIva(f.partita_iva)))
        e.partita_iva = "La partita IVA non è valida: verifica le 11 cifre.";
    } else if (PAESI_UE.has(f.paese)) {
      // Specchio del backend: si toglie il prefisso paese digitato (EL per la
      // Grecia) e si controlla la forma VIES (2-12 alfanumerici).
      const piva = normalizzaPivaUe(f.partita_iva, f.paese);
      if (!/^[A-Z0-9]{2,12}$/.test(piva))
        e.partita_iva = "Inserisci la partita IVA del tuo paese (2-12 caratteri).";
    } else if (f.partita_iva.trim().length < 2) {
      e.partita_iva = "Inserisci la partita IVA del tuo paese.";
    }
  } else {
    if (!f.nome.trim()) e.nome = "Inserisci il nome.";
    if (!f.cognome.trim()) e.cognome = "Inserisci il cognome.";
    // Il codice fiscale è richiesto solo per i privati italiani.
    if (isIt && !/^[A-Za-z0-9]{16}$/.test(f.codice_fiscale.trim()))
      e.codice_fiscale = "Il codice fiscale è di 16 caratteri.";
  }
  return e;
}

/** Corpo del PUT: solo i campi pertinenti al tipo (gli altri restano assenti,
 *  come si aspetta la validazione del backend). Provincia e CF viaggiano solo
 *  con paese IT. */
function toInput(f: FormState): BillingProfileInput {
  const isIt = f.paese === "IT";
  const base = {
    tipo_soggetto: f.tipo_soggetto,
    paese: f.paese,
    indirizzo: f.indirizzo.trim(),
    comune: f.comune.trim(),
    cap: f.cap.trim(),
    provincia: isIt ? f.provincia.trim().toUpperCase() : null,
  };
  if (f.tipo_soggetto === "azienda") {
    const piva = isIt
      ? normalizePartitaIva(f.partita_iva)
      : PAESI_UE.has(f.paese)
        ? normalizzaPivaUe(f.partita_iva, f.paese)
        : f.partita_iva.trim();
    return { ...base, denominazione: f.denominazione.trim(), partita_iva: piva };
  }
  return {
    ...base,
    nome: f.nome.trim(),
    cognome: f.cognome.trim(),
    codice_fiscale: isIt ? f.codice_fiscale.trim().toUpperCase() : null,
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
    }));
  };

  const setTipo = (tipo: TipoSoggetto) => {
    setErrors({});
    setForm((f) => ({ ...f, tipo_soggetto: tipo }));
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
  const isIt = form.paese === "IT";
  // Il VIES lo verifichiamo solo per le aziende UE ≠ HR: per HR (venditore),
  // extra-UE e privati non cambierebbe l'aliquota (25%).
  const mostraVies = tipo === "azienda" && viesApplicabile(form.paese);
  // Stato VIES salvato da mostrare (il profilo si aggiorna via setQueryData).
  const viesSalvato =
    profile?.tipo_soggetto === "azienda" && viesApplicabile(profile.paese)
      ? profile.vies_valid
      : undefined;

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
        <div className="mt-1.5 grid gap-2 sm:grid-cols-2">
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
        <SelectField
          label="Paese"
          required
          value={form.paese}
          error={errors.paese}
          onChange={(e) => setForm((f) => ({ ...f, paese: e.target.value }))}
        >
          {PAESI.map((code) => (
            <option key={code} value={code}>
              {nomePaese(code)}
            </option>
          ))}
        </SelectField>

        {mostraVies && (
          <p className="rounded-lg bg-brand-50 px-3 py-2 text-sm text-brand-700 sm:col-span-2">
            Al salvataggio verifichiamo la partita IVA nel VIES: se risulta valida, la
            fattura è emessa in reverse charge, senza IVA. Se il VIES non risponde, i
            dati vengono salvati comunque e agli acquisti si applica l'IVA al 25%.
          </p>
        )}

        {tipo === "azienda" ? (
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
              inputMode={isIt ? "numeric" : undefined}
              placeholder={isIt ? "11 cifre" : undefined}
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
            {isIt && (
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
            )}
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
        {/* Per l'Italia Provincia + CAP affiancati; per l'estero solo il CAP,
            a larghezza piena (niente colonna vuota accanto). */}
        {isIt ? (
          <div className="grid grid-cols-2 gap-4">
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
            <TextField
              label="CAP"
              required
              value={form.cap}
              error={errors.cap}
              onChange={set("cap")}
              autoComplete="postal-code"
              inputMode="numeric"
              maxLength={10}
            />
          </div>
        ) : (
          <TextField
            label="CAP"
            required
            value={form.cap}
            error={errors.cap}
            onChange={set("cap")}
            autoComplete="postal-code"
            maxLength={10}
          />
        )}

        <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
          <Button type="submit" loading={save.isPending}>
            Salva i dati
          </Button>
          {save.isPending && mostraVies && (
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
          {/* Esito VIES persistito (solo se il flash non è già visibile e il
              tipo/paese corrente prevede ancora il VIES: coerente con gli
              avvisi ambra/neutro sotto, che sono gated su mostraVies) */}
          {!savedFlash && mostraVies && viesSalvato === true && (
            <span className="inline-flex items-center gap-1.5 text-sm font-medium text-emerald-600">
              <ShieldCheck className="size-4" aria-hidden />
              P.IVA verificata nel VIES il {formatDate(profile!.vies_checked_at)}
            </span>
          )}
        </div>

        {/* Etichetta del venditore (per entrambi i tipi): chi eroga i servizi */}
        <p className="text-xs text-slate-400 sm:col-span-2">
          I servizi a pagamento sono erogati da: ADVENTUS CONSULTING j.d.o.o. Sede: Ulica
          1. svibnja - Via Primo Maggio 4, Umag / Umago, Croazia. OIB (IVA croato):
          95855486565
        </p>

        {/* Avvisi sull'esito VIES negativo/mancante: agli acquisti sarà 25% */}
        {!savedFlash && mostraVies && viesSalvato === false && (
          <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800 sm:col-span-2" role="status">
            La partita IVA non risulta valida nel VIES: agli acquisti si applica l'IVA al
            25%. Controlla la partita IVA e salva di nuovo per ripetere la verifica.
          </p>
        )}
        {!savedFlash && mostraVies && viesSalvato === null && (
          <p className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600 sm:col-span-2" role="status">
            Verifica VIES non riuscita: i dati sono salvati, ma senza esito positivo agli
            acquisti si applica l'IVA al 25%. Salva di nuovo per ritentare la verifica.
          </p>
        )}

        {save.isError && (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 sm:col-span-2" role="alert">
            {apiErrorMessage(save.error)}
          </p>
        )}
      </div>
    </form>
  );
}
