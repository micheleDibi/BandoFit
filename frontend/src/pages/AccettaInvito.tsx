import { AlertTriangle, Eye, EyeOff } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Logo } from "../components/layout/Logo";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { TextField } from "../components/ui/Field";
import { PasswordStrengthMeter } from "../components/ui/PasswordStrengthMeter";
import { api, apiErrorMessage } from "../lib/api";
import { supabase } from "../lib/supabase";

type Step = "loading" | "invalid" | "password" | "accepting" | "done";

interface InviteInfo {
  email: string;
  denominazione: string;
  parent_display_name: string;
}

/** Pagina di atterraggio del link d'invito azienda (token di dominio). */
export default function AccettaInvito() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");

  const [step, setStep] = useState<Step>(token ? "loading" : "invalid");
  const [invite, setInvite] = useState<InviteInfo | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    api
      .get<InviteInfo>("/auth/invite-info", { params: { token } })
      .then(({ data }) => {
        setInvite(data);
        setStep("password");
      })
      .catch(() => setStep("invalid"));
  }, [token]);

  const handleSubmit = async (e: FormEvent) => {
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
    setStep("accepting");
    try {
      const { data } = await api.post<{ email: string }>("/auth/accept-invite", {
        token,
        password,
      });
      setStep("done");
      // Auto-login con le credenziali appena impostate.
      const { error: signInError } = await supabase.auth.signInWithPassword({
        email: data.email,
        password,
      });
      setTimeout(
        () =>
          navigate(
            signInError ? `/login?email=${encodeURIComponent(data.email)}` : "/app/bandi",
            { replace: true },
          ),
        1500,
      );
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 404) {
        setStep("invalid");
        return;
      }
      setStep("password");
      setError(apiErrorMessage(err, "Qualcosa è andato storto, riprova."));
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
        {step === "loading" && (
          <div className="flex flex-col items-center py-8 text-center" role="status">
            <div className="size-8 animate-spin rounded-full border-3 border-brand-200 border-t-brand-500" />
            <p className="mt-4 text-sm text-slate-500">Verifica dell'invito in corso…</p>
          </div>
        )}

        {step === "invalid" && (
          <div className="flex flex-col items-center py-4 text-center">
            <div className="rounded-full bg-amber-100 p-3 text-amber-600">
              <AlertTriangle className="size-7" aria-hidden />
            </div>
            <h1 className="mt-4 font-display text-lg font-bold text-slate-900">
              Invito scaduto o non valido
            </h1>
            <p className="mt-2 text-sm text-slate-500">
              Il link che hai aperto non è più utilizzabile. Chiedi al titolare dell'azienda di
              reinviarti l'invito dalla sua pagina profilo.
            </p>
            <Link to="/" className="mt-6">
              <span className="text-sm font-medium text-brand-600 hover:underline">
                Torna alla home
              </span>
            </Link>
          </div>
        )}

        {(step === "password" || step === "accepting") && (
          <>
            <h1 className="font-display text-xl font-bold text-slate-900">Benvenuto su BandoFit</h1>
            <p className="mt-1 text-sm text-slate-500">
              <strong className="text-slate-700">{invite?.parent_display_name}</strong> ti ha
              invitato nella sua azienda come{" "}
              <strong className="text-slate-700">{invite?.denominazione}</strong>. Imposta la tua
              password per completare l'attivazione di{" "}
              <strong className="text-slate-700">{invite?.email}</strong>.
            </p>
            <form onSubmit={handleSubmit} className="mt-6 space-y-4" noValidate>
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
              <PasswordStrengthMeter
                password={password}
                userInputs={[invite?.email ?? "", invite?.denominazione ?? "", "bandofit"]}
              />
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
              <Button type="submit" className="w-full" size="lg" loading={step === "accepting"}>
                Attiva il mio account
              </Button>
            </form>
          </>
        )}

        {step === "done" && (
          <div className="flex flex-col items-center py-6 text-center" role="status">
            <div className="flex size-14 items-center justify-center rounded-full bg-emerald-100 text-emerald-600">
              <svg viewBox="0 0 24 24" fill="none" className="size-7" aria-hidden>
                <path
                  d="M5 13l4 4L19 7"
                  stroke="currentColor"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
            <h1 className="mt-4 font-display text-lg font-bold text-slate-900">
              Sei dentro{invite ? `, con ${invite.parent_display_name}` : ""}!
            </h1>
            <p className="mt-2 text-sm text-slate-500">Ti stiamo portando ai bandi…</p>
          </div>
        )}
      </Card>
    </div>
  );
}
