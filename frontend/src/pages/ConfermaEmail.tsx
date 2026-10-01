import { useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { AccessoLaterale } from "../components/landing/AccessoLaterale";
import { Alert } from "../components/ui/Alert";
import { AuthLayout } from "../components/ui/AuthLayout";
import { Button, LinkButton } from "../components/ui/Button";
import { TextField } from "../components/ui/Field";
import { PasswordField } from "../components/ui/PasswordField";
import { PasswordStrengthMeter } from "../components/ui/PasswordStrengthMeter";
import { TextLink } from "../components/ui/TextLink";
import { api, apiErrorCode, apiErrorMessage } from "../lib/api";

// «richiedi» non è uno stato d'errore: la registrazione non raccoglie più la
// password, quindi si arriva qui senza token anche dalle email legittime (es.
// «hai già una registrazione in attesa»). «invalid» è invece l'errore vero, e
// ci si arriva solo dopo un tentativo respinto con 404 (token morto).
type Step = "password" | "confirming" | "done" | "invalid" | "richiedi";

/** Atterraggio del link di conferma: qui si conferma l'indirizzo E si sceglie
 *  la password, che la registrazione non chiede più (anti-enumerazione). */
export default function ConfermaEmail() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");

  const [step, setStep] = useState<Step>(token ? "password" : "richiedi");
  const [confirmedEmail, setConfirmedEmail] = useState<string | null>(null);

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [resendEmail, setResendEmail] = useState("");
  const [resendState, setResendState] = useState<"idle" | "sending" | "sent">("idle");
  const [resendEmailError, setResendEmailError] = useState<string | null>(null);
  const [resendError, setResendError] = useState<string | null>(null);

  const handleConfirm = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setPasswordError(null);
    setConfirmError(null);
    if (password.length < 8) {
      setPasswordError("La password deve avere almeno 8 caratteri.");
      return;
    }
    if (password !== confirm) {
      setConfirmError("Le password non coincidono.");
      return;
    }
    setStep("confirming");
    try {
      const { data } = await api.post<{ email: string }>("/auth/confirm", { token, password });
      setConfirmedEmail(data.email);
      setStep("done");
    } catch (err) {
      // Distinguere conta: con un link morto ritentare la password è inutile e
      // l'unica uscita è chiederne uno nuovo; con una password rifiutata il
      // link è ancora valido (si consuma solo a conferma riuscita) e rimandare
      // al form di richiesta farebbe ricominciare da capo per niente.
      if (apiErrorCode(err) === "not_found") {
        setStep("invalid");
        return;
      }
      setStep("password");
      setError(apiErrorMessage(err, "Conferma non riuscita: riprova tra qualche istante."));
    }
  };

  const handleResend = async (e: FormEvent) => {
    e.preventDefault();
    setResendError(null);
    setResendEmailError(null);
    if (!/^\S+@\S+\.\S+$/.test(resendEmail)) {
      setResendEmailError("Inserisci un indirizzo email valido.");
      return;
    }
    setResendState("sending");
    try {
      await api.post("/auth/resend-confirmation", { email: resendEmail.trim() });
      setResendState("sent");
    } catch (err) {
      setResendState("idle");
      setResendError(
        apiErrorMessage(err, "Invio non riuscito: verifica l'indirizzo o riprova tra qualche minuto."),
      );
    }
  };

  const formRichiesta = (
    <form onSubmit={handleResend} className="mt-4 flex w-full flex-col gap-4" noValidate>
      <TextField
        label="Email"
        type="email"
        autoComplete="email"
        required
        value={resendEmail}
        onChange={(e) => setResendEmail(e.target.value)}
        placeholder="nome@azienda.it"
        error={resendEmailError ?? undefined}
      />
      {resendError && <Alert tono="errore">{resendError}</Alert>}
      <Button type="submit" className="w-full" loading={resendState === "sending"}>
        Inviami il link
      </Button>
    </form>
  );

  // Risposta neutra come /recupera-password: non diciamo se l'indirizzo esiste.
  const esitoRichiesta = (
    <p className="text-body text-ink-2" role="status">
      Se <strong className="font-semibold text-ink">{resendEmail}</strong> ha una registrazione
      da completare, riceverai a breve il link. Controlla anche lo spam.
    </p>
  );

  return (
    <AuthLayout laterale={<AccessoLaterale />}>
      {(step === "password" || step === "confirming") && (
        <>
          <h1 className="text-title-page text-ink">Completa la registrazione</h1>
          <p className="mt-1 text-body text-ink-2">
            Scegli la password del tuo account BandoFit: confermiamo il tuo indirizzo e sei dentro.
          </p>
          <form onSubmit={handleConfirm} className="mt-6 flex flex-col gap-4" noValidate>
            <div className="flex flex-col gap-2">
              <PasswordField
                label="Password"
                required
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                helper="Almeno 8 caratteri"
                error={passwordError ?? undefined}
              />
              <PasswordStrengthMeter password={password} userInputs={["bandofit"]} />
            </div>
            <PasswordField
              label="Conferma password"
              required
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              error={confirmError ?? undefined}
            />
            {error && <Alert tono="errore">{error}</Alert>}
            <Button
              type="submit"
              className="mt-2 w-full"
              size="lg"
              loading={step === "confirming"}
            >
              Attiva il mio account
            </Button>
          </form>
        </>
      )}

      {step === "done" && (
        <div className="flex flex-col items-start gap-2" role="status">
          <h1 className="text-title-page text-ink">Account attivo, benvenuto!</h1>
          <p className="text-body text-ink-2">
            Il tuo indirizzo è confermato: accedi con la password che hai appena scelto.
          </p>
          <LinkButton
            to={confirmedEmail ? `/login?email=${encodeURIComponent(confirmedEmail)}` : "/login"}
            className="mt-4"
          >
            Accedi
          </LinkButton>
        </div>
      )}

      {(step === "richiedi" || step === "invalid") && (
        <div className="flex flex-col items-start gap-2">
          <h1 className="text-title-page text-ink">
            {step === "richiedi"
              ? "Richiedi il link di conferma"
              : "Link di conferma scaduto o non valido"}
          </h1>
          {resendState === "sent" ? (
            esitoRichiesta
          ) : (
            <>
              <p className="text-body text-ink-2">
                {step === "richiedi"
                  ? "Inserisci il tuo indirizzo: ti mandiamo il link per confermarlo e scegliere la password."
                  : "Inserisci la tua email e ti inviamo un nuovo link."}
              </p>
              {formRichiesta}
            </>
          )}
          <TextLink to="/login" className="mt-4">
            Torna all'accesso
          </TextLink>
        </div>
      )}
    </AuthLayout>
  );
}
