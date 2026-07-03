import { AlertTriangle, MailCheck } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Logo } from "../components/layout/Logo";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { TextField } from "../components/ui/Field";
import { api, apiErrorMessage } from "../lib/api";

type Step = "confirming" | "done" | "invalid";

/** Pagina di atterraggio del link di conferma email (token di dominio). */
export default function ConfermaEmail() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");

  const [step, setStep] = useState<Step>(token ? "confirming" : "invalid");
  const [confirmedEmail, setConfirmedEmail] = useState<string | null>(null);
  const requested = useRef(false);

  const [resendEmail, setResendEmail] = useState("");
  const [resendState, setResendState] = useState<"idle" | "sending" | "sent">("idle");
  const [resendError, setResendError] = useState<string | null>(null);

  useEffect(() => {
    if (!token || requested.current) return;
    requested.current = true; // StrictMode: il token è monouso, una sola chiamata
    api
      .post<{ email: string }>("/auth/confirm", { token })
      .then(({ data }) => {
        setConfirmedEmail(data.email);
        setStep("done");
      })
      .catch(() => setStep("invalid"));
  }, [token]);

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

  return (
    <div className="flex min-h-dvh flex-col items-center justify-center bg-surface px-4 py-10">
      <Link
        to="/"
        className="mb-8 rounded-lg focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-brand-500"
      >
        <Logo variant="vertical" />
      </Link>
      <Card className="w-full max-w-md p-6 sm:p-8">
        {step === "confirming" && (
          <div className="flex flex-col items-center py-8 text-center" role="status">
            <div className="size-8 animate-spin rounded-full border-3 border-brand-200 border-t-brand-500" />
            <p className="mt-4 text-sm text-slate-500">Conferma in corso…</p>
          </div>
        )}

        {step === "done" && (
          <div className="flex flex-col items-center py-6 text-center" role="status">
            <div className="rounded-full bg-emerald-100 p-3 text-emerald-600">
              <MailCheck className="size-7" aria-hidden />
            </div>
            <h1 className="mt-4 font-display text-lg font-bold text-slate-900">
              Email confermata, benvenuto!
            </h1>
            <p className="mt-2 text-sm text-slate-500">
              Il tuo account è attivo: accedi con la tua password.
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

        {step === "invalid" && (
          <div className="flex flex-col items-center py-4 text-center">
            <div className="rounded-full bg-amber-100 p-3 text-amber-600">
              <AlertTriangle className="size-7" aria-hidden />
            </div>
            <h1 className="mt-4 font-display text-lg font-bold text-slate-900">
              Link di conferma scaduto o non valido
            </h1>
            {resendState === "sent" ? (
              <p className="mt-2 text-sm text-slate-500" role="status">
                Nuova email di conferma inviata a{" "}
                <strong className="text-slate-700">{resendEmail}</strong>: controlla la casella (e
                lo spam).
              </p>
            ) : (
              <>
                <p className="mt-2 text-sm text-slate-500">
                  Inserisci la tua email e ti inviamo un nuovo link di conferma.
                </p>
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
                    Reinvia email di conferma
                  </Button>
                </form>
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
