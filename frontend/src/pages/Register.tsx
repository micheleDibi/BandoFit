import { useState, type FormEvent, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { AccessoLaterale } from "../components/landing/AccessoLaterale";
import { Alert } from "../components/ui/Alert";
import { AuthLayout } from "../components/ui/AuthLayout";
import { Button } from "../components/ui/Button";
import { Combobox } from "../components/ui/Combobox";
import { TextField } from "../components/ui/Field";
import { TextLink } from "../components/ui/TextLink";
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
      Ti abbiamo scritto a{" "}
      <strong className="font-semibold text-ink">{form.email.trim()}</strong>: apri il messaggio
      per completare la registrazione e scegliere la password. Controlla anche lo spam.
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
    <AuthLayout laterale={<AccessoLaterale />}>
      <h1 className="text-title-page text-ink">Crea il tuo account</h1>
      <p className="mt-1 text-body text-ink-2">Compila i tuoi dati: ci vuole un minuto.</p>

      {/* Una colonna: il modulo di AuthLayout è largo 380px; solo nome e
          cognome, corti, stanno affiancati. */}
      <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-4" noValidate>
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
        {positionsError && (
          <Alert
            tono="errore"
            azione={
              <Button type="button" variant="ghost" size="sm" onClick={() => refetchPositions()}>
                Riprova
              </Button>
            }
          >
            Impossibile caricare le posizioni.
          </Alert>
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
        <p className="text-small text-ink-3">
          La password la scegli tra un momento, aprendo l'email di conferma.
        </p>

        <Button type="submit" className="w-full" size="lg" loading={loading} disabled={!!info}>
          Crea l'account
        </Button>
      </form>

      {pianoAPagamento && pianoRichiestoObj && (
        <Alert tono="info" className="mt-4">
          Completerai l'acquisto di {pianoRichiestoObj.nome} dopo il primo accesso: parti da
          Gratuito e lo attivi in un minuto.
        </Alert>
      )}

      {error && (
        <Alert tono="errore" className="mt-4">
          {error}
        </Alert>
      )}
      {info && (
        <Alert tono="info" className="mt-4">
          {info}
          {/* Le vie d'uscita: senza, chi sbaglia l'indirizzo o non riceve
              nulla resta fermo qui — il submit è disabilitato finché c'è
              un esito, e «Vai al login» non serve a chi un account non ce
              l'ha ancora. */}
          <div className="-ml-2 mt-2 flex flex-wrap items-center gap-x-2 gap-y-1">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={handleReinvia}
              disabled={loading}
            >
              Non è arrivata? Reinvia
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={handleCorreggi}>
              Ho sbagliato indirizzo
            </Button>
            <TextLink to="/login" className="px-2 text-small font-semibold">
              Vai al login
            </TextLink>
          </div>
        </Alert>
      )}

      <p className="mt-6 text-body text-ink-2">
        Hai già un account? <TextLink to="/login">Accedi</TextLink>
      </p>
    </AuthLayout>
  );
}
