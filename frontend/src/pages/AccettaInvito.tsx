import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { AccessoLaterale } from "../components/landing/AccessoLaterale";
import { Alert } from "../components/ui/Alert";
import { AuthLayout } from "../components/ui/AuthLayout";
import { Button } from "../components/ui/Button";
import { PasswordField } from "../components/ui/PasswordField";
import { PasswordStrengthMeter } from "../components/ui/PasswordStrengthMeter";
import { Spinner } from "../components/ui/Spinner";
import { TextLink } from "../components/ui/TextLink";
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
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
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
          navigate(signInError ? `/login?email=${encodeURIComponent(data.email)}` : "/app", {
            replace: true,
          }),
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
    <AuthLayout laterale={<AccessoLaterale />}>
      {step === "loading" && (
        <div className="flex items-center gap-3" role="status">
          <Spinner size="lg" />
          <p className="text-body text-ink-2">Verifica dell'invito in corso…</p>
        </div>
      )}

      {step === "invalid" && (
        <div className="flex flex-col items-start gap-2">
          <h1 className="text-title-page text-ink">Invito scaduto o non valido</h1>
          <p className="text-body text-ink-2">
            Il link che hai aperto non è più utilizzabile. Chiedi al titolare dell'azienda di
            reinviarti l'invito dalla sua pagina profilo.
          </p>
          <TextLink to="/" className="mt-4">
            Torna alla home
          </TextLink>
        </div>
      )}

      {(step === "password" || step === "accepting") && (
        <>
          <h1 className="text-title-page text-ink">Benvenuto su BandoFit</h1>
          <p className="mt-1 text-body text-ink-2">
            <strong className="font-semibold text-ink">{invite?.parent_display_name}</strong> ti
            ha invitato nella sua azienda come{" "}
            <strong className="font-semibold text-ink">{invite?.denominazione}</strong>. Imposta la
            tua password per completare l'attivazione di{" "}
            <strong className="font-semibold text-ink">{invite?.email}</strong>.
          </p>
          <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-4" noValidate>
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
              <PasswordStrengthMeter
                password={password}
                userInputs={[invite?.email ?? "", invite?.denominazione ?? "", "bandofit"]}
              />
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
              loading={step === "accepting"}
            >
              Attiva il mio account
            </Button>
          </form>
        </>
      )}

      {step === "done" && (
        <div className="flex flex-col items-start gap-2" role="status">
          <h1 className="text-title-page text-ink">
            Sei dentro{invite ? `, con ${invite.parent_display_name}` : ""}!
          </h1>
          <p className="text-body text-ink-2">Ti stiamo facendo entrare…</p>
        </div>
      )}
    </AuthLayout>
  );
}
