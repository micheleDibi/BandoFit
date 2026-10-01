import { useState, type FormEvent } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { AccessoLaterale } from "../components/landing/AccessoLaterale";
import { Alert } from "../components/ui/Alert";
import { AuthLayout } from "../components/ui/AuthLayout";
import { Button } from "../components/ui/Button";
import { TextField } from "../components/ui/Field";
import { PasswordField } from "../components/ui/PasswordField";
import { TextLink } from "../components/ui/TextLink";
import { api } from "../lib/api";
import { supabase } from "../lib/supabase";

/** Destinazione dopo l'accesso (`state.from` del guard o `?next=`): vale solo
 *  dentro l'app, `/app`, `/app/…`, `/app?…`, `/app#…`. Un controllo sul
 *  prefisso non basta: `/app/../`, `/app/..//host` e la barra rovesciata
 *  portano fuori (fino a un altro sito). Si scarta quindi ogni `\` (anche
 *  codificata come `%5C`), si fa normalizzare l'indirizzo al parser del browser
 *  (`..`, `%2e%2e`) e lo si accetta solo se resta su questa origine e sotto
 *  `/app`; si restituisce la forma normalizzata, mai la stringa ricevuta. */
function percorsoInterno(valore: unknown): string | null {
  if (typeof valore !== "string" || !valore.startsWith("/")) return null;
  if (valore.includes("\\") || /%5c/i.test(valore)) return null;
  try {
    const url = new URL(valore, window.location.origin);
    if (url.origin !== window.location.origin) return null;
    if (url.pathname !== "/app" && !url.pathname.startsWith("/app/")) return null;
    return url.pathname + url.search + url.hash;
  } catch {
    return null;
  }
}

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  // Prefill dell'email dai flussi di conferma/reset (?email=...)
  const [email, setEmail] = useState(() => searchParams.get("email") ?? "");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notConfirmed, setNotConfirmed] = useState(false);
  const [resendState, setResendState] = useState<"idle" | "sending" | "sent">("idle");
  const [loading, setLoading] = useState(false);

  // Prima la pagina da cui il guard ci ha mandati qui (con query e hash), poi
  // un `?next=`, altrimenti la Home: entrambi passano dallo stesso controllo.
  const from =
    percorsoInterno((location.state as { from?: unknown } | null)?.from) ??
    percorsoInterno(searchParams.get("next")) ??
    "/app";

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
      } else if (authError.message === "Invalid login credentials") {
        // Chi si è registrato e non ha ancora confermato NON ha una password
        // (si sceglie confermando), quindi finisce qui e non nel ramo «not
        // confirmed»: senza offrire anche qui il reinvio, il caso più frequente
        // resterebbe senza via d'uscita. L'endpoint di reinvio è neutro, quindi
        // mostrarlo non rivela se l'account esiste.
        setNotConfirmed(true);
        setError(
          "Email o password non corretti. Se non hai ancora confermato il tuo " +
            "indirizzo, richiedi un nuovo link.",
        );
      } else {
        setError("Accesso non riuscito. Riprova tra qualche istante.");
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
    <AuthLayout laterale={<AccessoLaterale />}>
      <h1 className="text-title-page text-ink">Accedi</h1>

      <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-4" noValidate>
        <TextField
          label="Email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="nome@azienda.it"
        />
        <div className="flex flex-col gap-2">
          <PasswordField
            label="Password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="La tua password"
          />
          <TextLink to="/recupera-password" className="self-start text-small">
            Password dimenticata?
          </TextLink>
        </div>

        {error && (
          <Alert tono="errore">
            {error}
            {notConfirmed && (
              <div className="mt-1">
                {resendState === "sent" ? (
                  <span className="font-medium text-fit-ink" role="status">
                    Email di conferma reinviata!
                  </span>
                ) : (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="-ml-2"
                    onClick={handleResendConfirmation}
                    loading={resendState === "sending"}
                  >
                    {resendState === "sending" ? "Invio in corso…" : "Inviami il link di conferma"}
                  </Button>
                )}
              </div>
            )}
          </Alert>
        )}

        <Button type="submit" className="mt-2 w-full" size="lg" loading={loading}>
          Accedi
        </Button>
      </form>

      <p className="mt-6 text-body text-ink-2">
        Non hai un account? <TextLink to="/registrati">Registrati gratis</TextLink>
      </p>
    </AuthLayout>
  );
}
