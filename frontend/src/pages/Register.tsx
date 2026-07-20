import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Logo } from "../components/layout/Logo";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Combobox } from "../components/ui/Combobox";
import { TextField } from "../components/ui/Field";
import { useJobPositions } from "../hooks/useJobPositions";
import { usePlans } from "../hooks/usePlans";
import { api, apiErrorMessage } from "../lib/api";
import { isValidTelefono, normalizeTelefono } from "../lib/telefono";

// Niente password qui: si sceglie aprendo il link di conferma (/conferma-email).
// Non è una scelta di UX — è ciò che impedisce di scoprire se un indirizzo è
// registrato, vedi la docstring di backend/app/services/auth_service.
interface FormData {
  nome: string;
  cognome: string;
  azienda: string;
  telefono: string;
  email: string;
}

const EMPTY_FORM: FormData = {
  nome: "",
  cognome: "",
  azienda: "",
  telefono: "",
  email: "",
};

type FieldErrors = Partial<Record<keyof FormData | "posizione", string>>;

export default function Register() {
  const [searchParams] = useSearchParams();
  // I piani servono solo a qualificare il ?piano= della query string (vedi
  // sotto): il form non li mostra — il piano non si sceglie più qui, perché
  // l'assegnazione è comunque server-side (i piani a pagamento partono da
  // Gratuito e si comprano dal checkout dopo il primo accesso).
  const { data: plans } = usePlans();
  const {
    data: positions,
    isError: positionsError,
    refetch: refetchPositions,
  } = useJobPositions();

  const [form, setForm] = useState<FormData>(EMPTY_FORM);
  const [positionId, setPositionId] = useState<number | null>(null);
  const [posizioneAltro, setPosizioneAltro] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [error, setError] = useState<string | null>(null);
  // ReactNode e non string: il pannello di esito evidenzia l'indirizzo e offre
  // le vie d'uscita (reinvio, correzione), che sono elementi, non testo.
  const [info, setInfo] = useState<ReactNode>(null);
  const [loading, setLoading] = useState(false);

  // Il piano arrivato dalla landing (?piano=) parte nel payload come INTENTO:
  // non è più uno stato selezionabile. Un piano «su richiesta» però non si
  // sceglie alla registrazione (il backend risponderebbe 400): si ripiega su
  // gratuito, come faceva la vecchia griglia. Uno slug ignoto resta com'è —
  // il trigger handle_new_user ripiega da sé sul piano Gratuito.
  const pianoRichiesto = searchParams.get("piano") ?? "gratuito";
  const pianoRichiestoObj = plans?.find((p) => p.slug === pianoRichiesto) ?? null;
  const planSlug =
    pianoRichiestoObj?.tipo_prezzo === "su_richiesta" ? "gratuito" : pianoRichiesto;
  // Un piano a pagamento non si attiva alla registrazione: la riga sotto il
  // submit lo dice prima che l'utente se lo aspetti attivo.
  const pianoAPagamento =
    !!pianoRichiestoObj &&
    pianoRichiestoObj.tipo_prezzo === "importo" &&
    Number(pianoRichiestoObj.prezzo_annuale) > 0;

  const set = (key: keyof FormData) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  const selectedPosition = positions?.find((p) => p.id === positionId) ?? null;

  const validate = (): boolean => {
    const errors: FieldErrors = {};
    if (!form.nome.trim()) errors.nome = "Il nome è obbligatorio.";
    if (!form.cognome.trim()) errors.cognome = "Il cognome è obbligatorio.";
    if (!form.telefono.trim()) {
      errors.telefono = "Il numero di telefono è obbligatorio.";
    } else if (!isValidTelefono(normalizeTelefono(form.telefono))) {
      errors.telefono = "Inserisci un numero di telefono valido (es. 347 1234567).";
    }
    // `selectedPosition` e non `positionId`: la voce può sparire dal catalogo
    // tra la scelta e il submit (voce disattivata + refetch) — mai degradare
    // a slug vuoto (422 generico), l'errore va sul campo.
    if (!selectedPosition) errors.posizione = "Seleziona la tua posizione in azienda.";
    if (!/^\S+@\S+\.\S+$/.test(form.email)) errors.email = "Inserisci un indirizzo email valido.";
    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleCorreggi = () => {
    // L'esito va ripulito: il submit si disabilita finché `info` è valorizzato,
    // quindi senza questo chi corregge un indirizzo sbagliato resterebbe
    // bloccato fino a un ricaricamento della pagina.
    setInfo(null);
    setError(null);
  };

  const handleReinvia = async () => {
    setError(null);
    setLoading(true);
    try {
      // Endpoint già neutro, e con un cooldown suo: chi non ha ricevuto nulla
      // ritenta da qui senza consumare il budget della registrazione.
      await api.post("/auth/resend-confirmation", { email: form.email.trim() });
    } catch {
      // Risposta neutra per definizione: non c'è nulla di utile da mostrare.
    }
    setLoading(false);
    setInfo(esitoRegistrazione());
  };

  // Neutro di proposito: non dice se l'account è stato creato o esisteva già —
  // quella risposta sta nell'email, che raggiunge solo chi possiede la casella.
  // Modello: RecuperaPassword.
  const esitoRegistrazione = (): ReactNode => (
    <>
      Ti abbiamo scritto a <strong className="text-slate-900">{form.email.trim()}</strong>: apri il
      messaggio per completare la registrazione e scegliere la password. Controlla anche lo spam.
    </>
  );

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    // Il secondo controllo è ridondante a runtime (validate lo copre) ma
    // serve al narrowing: dopo, selectedPosition è certamente valorizzata.
    if (!validate() || !selectedPosition) return;
    setInfo(null);
    setLoading(true);
    try {
      // La registrazione passa dal backend: l'email di conferma parte dal
      // NOSTRO provider (SMTP/OVH), mai dal mailer di Supabase. La risposta è
      // sempre 202 {"ok": true}, identica per un indirizzo nuovo e per uno già
      // registrato: qui non c'è nulla da ispezionare, e non deve essercene.
      await api.post("/auth/register", {
        email: form.email.trim(),
        nome: form.nome.trim(),
        cognome: form.cognome.trim(),
        azienda: form.azienda.trim() || null,
        telefono: normalizeTelefono(form.telefono),
        job_position_slug: selectedPosition.slug,
        job_position_altro:
          selectedPosition.slug === "altro" ? posizioneAltro.trim() || null : null,
        plan_slug: planSlug,
      });
      setLoading(false);
      setInfo(esitoRegistrazione());
    } catch (err) {
      setLoading(false);
      setError(apiErrorMessage(err, "Registrazione non riuscita. Riprova tra qualche istante."));
    }
  };

  return (
    <div className="flex min-h-dvh flex-col items-center bg-surface px-4 py-10">
      <Link to="/" className="mb-8 rounded-lg focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-brand-500">
        <Logo variant="vertical" />
      </Link>

      <Card className="w-full max-w-lg p-6 sm:p-8">
        <h1 className="font-display text-xl font-bold text-slate-900">Crea il tuo account</h1>
        <p className="mt-1 text-sm text-slate-500">Compila i tuoi dati: ci vuole un minuto.</p>

        <form onSubmit={handleSubmit} className="mt-6 space-y-4" noValidate>
          <div className="grid grid-cols-2 gap-3">
            <TextField
              label="Nome"
              required
              autoComplete="given-name"
              value={form.nome}
              onChange={set("nome")}
              error={fieldErrors.nome}
            />
            <TextField
              label="Cognome"
              required
              autoComplete="family-name"
              value={form.cognome}
              onChange={set("cognome")}
              error={fieldErrors.cognome}
            />
          </div>
          {/* Contatti: email e telefono affiancati (impilati su mobile). */}
          <div className="grid gap-4 sm:grid-cols-2 sm:gap-3">
            <TextField
              label="Email"
              type="email"
              required
              autoComplete="email"
              value={form.email}
              onChange={set("email")}
              error={fieldErrors.email}
              placeholder="nome@azienda.it"
            />
            <TextField
              label="Telefono"
              type="tel"
              required
              autoComplete="tel"
              value={form.telefono}
              onChange={set("telefono")}
              error={fieldErrors.telefono}
              placeholder="347 1234567"
              helper={!fieldErrors.telefono ? "Prefisso +39 automatico" : undefined}
            />
          </div>
          {/* Azienda: nome e posizione sono una coppia semantica. */}
          <div className="grid gap-4 sm:grid-cols-2 sm:gap-3">
            <TextField
              label="Azienda"
              autoComplete="organization"
              value={form.azienda}
              onChange={set("azienda")}
              helper="Facoltativa"
            />
            <Combobox
              label="Posizione in azienda"
              required
              options={(positions ?? []).map((p) => ({ id: p.id, label: p.nome }))}
              value={positionId}
              onChange={setPositionId}
              placeholder="Cerca…"
              disabled={!positions}
              error={fieldErrors.posizione}
            />
          </div>
          {positionsError && (
            <p className="text-sm text-red-600" role="alert">
              Impossibile caricare le posizioni.{" "}
              <button
                type="button"
                onClick={() => refetchPositions()}
                className="cursor-pointer font-medium underline underline-offset-2"
              >
                Riprova
              </button>
            </p>
          )}
          {selectedPosition?.slug === "altro" && (
            <TextField
              label="Specifica la posizione"
              value={posizioneAltro}
              onChange={(e) => setPosizioneAltro(e.target.value)}
              helper="Facoltativa"
              maxLength={100}
            />
          )}
          <p className="text-xs text-slate-400">
            La password la scegli tra un momento, aprendo l'email di conferma.
          </p>

          <Button type="submit" className="w-full" size="lg" loading={loading} disabled={!!info}>
            Crea l'account
          </Button>
        </form>

        {pianoAPagamento && pianoRichiestoObj && (
          <p className="mt-4 rounded-lg bg-brand-50 px-4 py-3 text-sm text-brand-800" role="note">
            Completerai l'acquisto di {pianoRichiestoObj.nome} dopo il primo accesso: parti da
            Gratuito e lo attivi in un minuto.
          </p>
        )}

        {error && (
          <p className="mt-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">
            {error}
          </p>
        )}
        {info && (
          <div className="mt-4 rounded-lg bg-brand-50 px-4 py-3 text-sm text-brand-800" role="status">
            {info}
            {/* Le vie d'uscita: senza, chi sbaglia l'indirizzo o non riceve
                nulla resta fermo qui — il submit è disabilitato finché c'è
                un esito, e «Vai al login» non serve a chi un account non ce
                l'ha ancora. */}
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 font-medium">
              <button
                type="button"
                onClick={handleReinvia}
                disabled={loading}
                className="cursor-pointer underline underline-offset-2 disabled:opacity-50"
              >
                Non è arrivata? Reinvia
              </button>
              <button
                type="button"
                onClick={handleCorreggi}
                className="cursor-pointer underline underline-offset-2"
              >
                Ho sbagliato indirizzo
              </button>
              <Link to="/login" className="underline underline-offset-2">
                Vai al login
              </Link>
            </div>
          </div>
        )}

        <p className="mt-6 text-center text-sm text-slate-500">
          Hai già un account?{" "}
          <Link to="/login" className="font-medium text-brand-600 underline-offset-2 hover:underline">
            Accedi
          </Link>
        </p>
      </Card>
    </div>
  );
}
