import { AlertTriangle, Eye, EyeOff } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Logo } from "../components/layout/Logo";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { TextField } from "../components/ui/Field";
import { api, apiErrorMessage } from "../lib/api";
import { supabase } from "../lib/supabase";

/** Pagina di atterraggio del link "reimposta password" (token di dominio). */
export default function ReimpostaPassword() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const [expired, setExpired] = useState(!token);

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
    setSaving(true);
    try {
      const { data } = await api.post<{ email: string }>("/auth/reset", { token, password });
      setDone(true);
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
        1200,
      );
    } catch (err) {
      setSaving(false);
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 404) {
        setExpired(true);
        return;
      }
      setError(apiErrorMessage(err, "Aggiornamento non riuscito, riprova."));
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
        {expired ? (
          <div className="flex flex-col items-center py-4 text-center">
            <div className="rounded-full bg-amber-100 p-3 text-amber-600">
              <AlertTriangle className="size-7" aria-hidden />
            </div>
            <h1 className="mt-4 font-display text-lg font-bold text-slate-900">
              Link scaduto o non valido
            </h1>
            <p className="mt-2 text-sm text-slate-500">
              I link di recupero valgono una sola volta e per un'ora. Richiedine uno nuovo.
            </p>
            <LinkButton to="/recupera-password" className="mt-6">
              Richiedi un nuovo link
            </LinkButton>
          </div>
        ) : done ? (
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
              Password aggiornata
            </h1>
            <p className="mt-2 text-sm text-slate-500">Ti stiamo facendo entrare…</p>
          </div>
        ) : (
          <>
            <h1 className="font-display text-xl font-bold text-slate-900">
              Imposta la nuova password
            </h1>
            <form onSubmit={handleSubmit} className="mt-6 space-y-4" noValidate>
              <div className="relative">
                <TextField
                  label="Nuova password"
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
              <Button type="submit" className="w-full" size="lg" loading={saving}>
                Salva la nuova password
              </Button>
            </form>
          </>
        )}
      </Card>
    </div>
  );
}
