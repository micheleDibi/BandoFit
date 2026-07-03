import { AlertTriangle, MailCheck } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Logo } from "../components/layout/Logo";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { TextField } from "../components/ui/Field";
import { useHashSession } from "../hooks/useHashSession";
import { api, apiErrorMessage } from "../lib/api";

/** Pagina di atterraggio del link "conferma email" dopo la registrazione. */
export default function ConfermaEmail() {
  const navigate = useNavigate();
  const hashSession = useHashSession();

  const [resendEmail, setResendEmail] = useState("");
  const [resendState, setResendState] = useState<"idle" | "sending" | "sent">("idle");
  const [resendError, setResendError] = useState<string | null>(null);

  // Email confermata → sessione attiva → dritto ai bandi.
  useEffect(() => {
    if (hashSession === "ready") {
      const timer = setTimeout(() => navigate("/app/bandi", { replace: true }), 1500);
      return () => clearTimeout(timer);
    }
  }, [hashSession, navigate]);

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
        {hashSession === "waiting" && (
          <div className="flex flex-col items-center py-8 text-center" role="status">
            <div className="size-8 animate-spin rounded-full border-3 border-brand-200 border-t-brand-500" />
            <p className="mt-4 text-sm text-slate-500">Conferma in corso…</p>
          </div>
        )}

        {hashSession === "ready" && (
          <div className="flex flex-col items-center py-6 text-center" role="status">
            <div className="rounded-full bg-emerald-100 p-3 text-emerald-600">
              <MailCheck className="size-7" aria-hidden />
            </div>
            <h1 className="mt-4 font-display text-lg font-bold text-slate-900">
              Email confermata, benvenuto!
            </h1>
            <p className="mt-2 text-sm text-slate-500">Ti stiamo portando ai bandi…</p>
          </div>
        )}

        {hashSession === "invalid" && (
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
            <Link
              to="/login"
              className="mt-5 text-sm font-medium text-brand-600 underline-offset-2 hover:underline"
            >
              Torna all'accesso
            </Link>
          </div>
        )}
      </Card>
    </div>
  );
}
