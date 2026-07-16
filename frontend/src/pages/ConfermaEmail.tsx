import { AlertTriangle, Eye, EyeOff, MailCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Logo } from "../components/layout/Logo";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { TextField } from "../components/ui/Field";
import { PasswordStrengthMeter } from "../components/ui/PasswordStrengthMeter";
import { api, apiErrorCode, apiErrorMessage } from "../lib/api";

// «richiedi» non è uno stato d'errore: la registrazione non raccoglie più la
// password, quindi si arriva qui senza token anche dalle email legittime (es.
// «hai già una registrazione in attesa»). «invalid» è invece l'errore vero, e
// ci si arriva solo dopo un tentativo respinto con 404 (token morto).
type Step = "password" | "confirming" | "done" | "invalid" | "richiedi";

/** Atterraggio del link di conferma: qui si conferma l'indirizzo E si sceglie
 *  la password, che la registrazione non chiede più (anti-enumerazione). */
export default function ConfermaEmail() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");

  const [step, setStep] = useState<Step>(token ? "password" : "richiedi");
  const [confirmedEmail, setConfirmedEmail] = useState<string | null>(null);

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [resendEmail, setResendEmail] = useState("");
  const [resendState, setResendState] = useState<"idle" | "sending" | "sent">("idle");
  const [resendError, setResendError] = useState<string | null>(null);

  const handleConfirm = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (password.length < 8) {
      setError("La password deve avere almeno 8 caratteri.");
      return;
    }
    if (password !== confirm) {
      setError("Le password non coincidono.");
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
    if (!/^\S+@\S+\.\S+$/.test(resendEmail)) {
      setResendError("Inserisci un indirizzo email valido.");
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
    <form onSubmit={handleResend} className="mt-5 w-full space-y-3 text-left" noValidate>
      <TextField
        label="Email"
        type="email"
        autoComplete="email"
        required
        value={resendEmail}
        onChange={(e) => setResendEmail(e.target.value)}
        placeholder="nome@azienda.it"
      />
      {resendError && (
        <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {resendError}
        </p>
      )}
      <Button type="submit" className="w-full" loading={resendState === "sending"}>
        Inviami il link
      </Button>
    </form>
  );

  // Risposta neutra come /recupera-password: non diciamo se l'indirizzo esiste.
  const esitoRichiesta = (
    <p className="mt-2 text-sm text-slate-500" role="status">
      Se <strong className="text-slate-700">{resendEmail}</strong> ha una registrazione da
      completare, riceverai a breve il link. Controlla anche lo spam.
    </p>
  );

  return (
    <div className="flex min-h-dvh flex-col items-center justify-center bg-surface px-4 py-10">
      <Link
        to="/"
        className="mb-8 rounded-lg focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-brand-500"
      >
        <Logo variant="vertical" />
      </Link>
      <Card className="w-full max-w-md p-6 sm:p-8">
        {(step === "password" || step === "confirming") && (
          <>
            <h1 className="font-display text-xl font-bold text-slate-900">
              Completa la registrazione
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              Scegli la password del tuo account BandoFit: confermiamo il tuo indirizzo e sei
              dentro.
            </p>
            <form onSubmit={handleConfirm} className="mt-6 space-y-4" noValidate>
              <div className="relative">
                <TextField
                  label="Password"
                  type={showPassword ? "text" : "password"}
                  required
                  autoComplete="new-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  helper="Almeno 8 caratteri"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? "Nascondi password" : "Mostra password"}
                  className="absolute right-2 top-9 cursor-pointer rounded-md p-1.5 text-slate-400 transition-colors hover:text-slate-600 focus-visible:outline-2 focus-visible:outline-brand-500"
                >
                  {showPassword ? (
                    <EyeOff className="size-4" aria-hidden />
                  ) : (
                    <Eye className="size-4" aria-hidden />
                  )}
                </button>
              </div>
              <PasswordStrengthMeter password={password} userInputs={["bandofit"]} />
              <TextField
                label="Conferma password"
                type={showPassword ? "text" : "password"}
                required
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
              />
              {error && (
                <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
                  {error}
                </p>
              )}
              <Button type="submit" className="w-full" size="lg" loading={step === "confirming"}>
                Attiva il mio account
              </Button>
            </form>
          </>
        )}

        {step === "done" && (
          <div className="flex flex-col items-center py-6 text-center" role="status">
            <div className="rounded-full bg-emerald-100 p-3 text-emerald-600">
              <MailCheck className="size-7" aria-hidden />
            </div>
            <h1 className="mt-4 font-display text-lg font-bold text-slate-900">
              Account attivo, benvenuto!
            </h1>
            <p className="mt-2 text-sm text-slate-500">
              Il tuo indirizzo è confermato: accedi con la password che hai appena scelto.
            </p>
            <Button
              className="mt-6"
              onClick={() =>
                navigate(
                  confirmedEmail ? `/login?email=${encodeURIComponent(confirmedEmail)}` : "/login",
                )
              }
            >
              Accedi
            </Button>
          </div>
        )}

        {step === "richiedi" && (
          <div className="flex flex-col items-center py-4 text-center">
            <div className="rounded-full bg-brand-50 p-3 text-brand-600">
              <MailCheck className="size-7" aria-hidden />
            </div>
            <h1 className="mt-4 font-display text-lg font-bold text-slate-900">
              Richiedi il link di conferma
            </h1>
            {resendState === "sent" ? (
              esitoRichiesta
            ) : (
              <>
                <p className="mt-2 text-sm text-slate-500">
                  Inserisci il tuo indirizzo: ti mandiamo il link per confermarlo e scegliere la
                  password.
                </p>
                {formRichiesta}
              </>
            )}
            <LinkButton to="/login" variant="ghost" className="mt-5">
              Torna all'accesso
            </LinkButton>
          </div>
        )}

        {step === "invalid" && (
          <div className="flex flex-col items-center py-4 text-center">
            <div className="rounded-full bg-amber-100 p-3 text-amber-600">
              <AlertTriangle className="size-7" aria-hidden />
            </div>
            <h1 className="mt-4 font-display text-lg font-bold text-slate-900">
              Link di conferma scaduto o non valido
            </h1>
            {resendState === "sent" ? (
              esitoRichiesta
            ) : (
              <>
                <p className="mt-2 text-sm text-slate-500">
                  Inserisci la tua email e ti inviamo un nuovo link.
                </p>
                {formRichiesta}
              </>
            )}
            <LinkButton to="/login" variant="ghost" className="mt-5">
              Torna all'accesso
            </LinkButton>
          </div>
        )}
      </Card>
    </div>
  );
}
