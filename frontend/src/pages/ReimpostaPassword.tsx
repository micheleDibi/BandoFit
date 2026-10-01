import { CircleCheck, Link2Off } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { AccessoLaterale } from "../components/landing/AccessoLaterale";
import { Alert } from "../components/ui/Alert";
import { AuthLayout } from "../components/ui/AuthLayout";
import { Button, LinkButton } from "../components/ui/Button";
import { IconChip } from "../components/ui/IconChip";
import { PasswordField } from "../components/ui/PasswordField";
import { PasswordStrengthMeter } from "../components/ui/PasswordStrengthMeter";
import { api, apiErrorMessage } from "../lib/api";
import { supabase } from "../lib/supabase";

/** Pagina di atterraggio del link "reimposta password" (token di dominio). */
export default function ReimpostaPassword() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const [expired, setExpired] = useState(!token);

  const handleSubmit = async (e: FormEvent) => {
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
          navigate(signInError ? `/login?email=${encodeURIComponent(data.email)}` : "/app", {
            replace: true,
          }),
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
    <AuthLayout laterale={<AccessoLaterale />}>
      {expired ? (
        <div className="flex flex-col items-start gap-2">
          <IconChip icon={Link2Off} size="lg" className="mb-2" />
          <h1 className="text-title-page text-ink">Link scaduto o non valido</h1>
          <p className="text-body text-ink-2">
            I link di recupero valgono una sola volta e per un'ora. Richiedine uno nuovo.
          </p>
          <LinkButton to="/recupera-password" className="mt-4">
            Richiedi un nuovo link
          </LinkButton>
        </div>
      ) : done ? (
        <div className="flex flex-col items-start gap-2" role="status">
          <IconChip icon={CircleCheck} size="lg" className="mb-2" />
          <h1 className="text-title-page text-ink">Password aggiornata</h1>
          <p className="text-body text-ink-2">Ti stiamo facendo entrare…</p>
        </div>
      ) : (
        <>
          <h1 className="text-title-page text-ink">Imposta la nuova password</h1>
          <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-4" noValidate>
            <div className="flex flex-col gap-2">
              <PasswordField
                label="Nuova password"
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
            <Button type="submit" className="mt-2 w-full" size="lg" loading={saving}>
              Salva la nuova password
            </Button>
          </form>
        </>
      )}
    </AuthLayout>
  );
}
