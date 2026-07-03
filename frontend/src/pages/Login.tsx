import { Eye, EyeOff } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Logo } from "../components/layout/Logo";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { TextField } from "../components/ui/Field";
import { api } from "../lib/api";
import { supabase } from "../lib/supabase";

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notConfirmed, setNotConfirmed] = useState(false);
  const [resendState, setResendState] = useState<"idle" | "sending" | "sent">("idle");
  const [loading, setLoading] = useState(false);

  const from = (location.state as { from?: string } | null)?.from ?? "/app/bandi";

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setNotConfirmed(false);
    setLoading(true);
    const { error: authError } = await supabase.auth.signInWithPassword({ email, password });
    setLoading(false);
    if (authError) {
      if (authError.message.toLowerCase().includes("not confirmed")) {
        setNotConfirmed(true);
        setError("Devi prima confermare la tua email: controlla la casella (e lo spam).");
      } else {
        setError(
          authError.message === "Invalid login credentials"
            ? "Email o password non corretti."
            : "Accesso non riuscito. Riprova tra qualche istante.",
        );
      }
      return;
    }
    navigate(from, { replace: true });
  };

  const handleResendConfirmation = async () => {
    setResendState("sending");
    try {
      await api.post("/auth/resend-confirmation", { email: email.trim() });
      setResendState("sent");
    } catch {
      setResendState("idle");
      setError("Reinvio non riuscito, riprova tra qualche minuto.");
    }
  };

  return (
    <div className="flex min-h-dvh flex-col items-center justify-center bg-surface px-4 py-10">
      <Link to="/" className="mb-8 rounded-lg focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-brand-500">
        <Logo />
      </Link>
      <Card className="w-full max-w-md p-6 sm:p-8">
        <h1 className="font-display text-xl font-bold text-slate-900">Bentornato</h1>
        <p className="mt-1 text-sm text-slate-500">Accedi per consultare i bandi.</p>

        <form onSubmit={handleSubmit} className="mt-6 space-y-4" noValidate>
          <TextField
            label="Email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="nome@azienda.it"
          />
          <div className="relative">
            <TextField
              label="Password"
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="La tua password"
            />
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              aria-label={showPassword ? "Nascondi password" : "Mostra password"}
              className="absolute right-2 top-9 cursor-pointer rounded-md p-1.5 text-slate-400 transition-colors hover:text-slate-600 focus-visible:outline-2 focus-visible:outline-brand-500"
            >
              {showPassword ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
            </button>
          </div>

          <div className="text-right">
            <Link
              to="/recupera-password"
              className="text-sm font-medium text-brand-600 underline-offset-2 hover:underline"
            >
              Password dimenticata?
            </Link>
          </div>

          {error && (
            <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
              {error}
              {notConfirmed && (
                <div className="mt-2">
                  {resendState === "sent" ? (
                    <span className="font-medium text-emerald-700" role="status">
                      Email di conferma reinviata!
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={handleResendConfirmation}
                      disabled={resendState === "sending"}
                      className="cursor-pointer font-medium underline underline-offset-2 disabled:opacity-50"
                    >
                      {resendState === "sending" ? "Invio in corso…" : "Reinvia email di conferma"}
                    </button>
                  )}
                </div>
              )}
            </div>
          )}

          <Button type="submit" className="w-full" size="lg" loading={loading}>
            Accedi
          </Button>
        </form>

        <p className="mt-6 text-center text-sm text-slate-500">
          Non hai un account?{" "}
          <Link
            to="/registrati"
            className="font-medium text-brand-600 underline-offset-2 hover:underline"
          >
            Registrati gratis
          </Link>
        </p>
      </Card>
    </div>
  );
}
