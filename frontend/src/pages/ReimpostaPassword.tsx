import { AlertTriangle, Eye, EyeOff } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Logo } from "../components/layout/Logo";
import { PoweredBy } from "../components/shared/PoweredBy";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { TextField } from "../components/ui/Field";
import { useHashSession } from "../hooks/useHashSession";
import { supabase } from "../lib/supabase";

/** Pagina di atterraggio del link "reimposta password" di Supabase. */
export default function ReimpostaPassword() {
  const navigate = useNavigate();
  const hashSession = useHashSession();

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);

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
    const { error: updateError } = await supabase.auth.updateUser({ password });
    setSaving(false);
    if (updateError) {
      setError(
        updateError.message.includes("different from the old")
          ? "La nuova password deve essere diversa da quella attuale."
          : "Aggiornamento non riuscito, riprova.",
      );
      return;
    }
    setDone(true);
    setTimeout(() => navigate("/app/bandi", { replace: true }), 1500);
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
            <p className="mt-4 text-sm text-slate-500">Verifica del link in corso…</p>
          </div>
        )}

        {hashSession === "invalid" && (
          <div className="flex flex-col items-center py-4 text-center">
            <div className="rounded-full bg-amber-100 p-3 text-amber-600">
              <AlertTriangle className="size-7" aria-hidden />
            </div>
            <h1 className="mt-4 font-display text-lg font-bold text-slate-900">
              Link scaduto o non valido
            </h1>
            <p className="mt-2 text-sm text-slate-500">
              I link di recupero valgono una sola volta e per un tempo limitato. Richiedine uno
              nuovo.
            </p>
            <LinkButton to="/recupera-password" className="mt-6">
              Richiedi un nuovo link
            </LinkButton>
          </div>
        )}

        {hashSession === "ready" && !done && (
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

        {done && (
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
            <p className="mt-2 text-sm text-slate-500">Ti stiamo portando ai bandi…</p>
          </div>
        )}
      </Card>
      <PoweredBy className="mt-8" />
    </div>
  );
}
