import { MailCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import { AccessoLaterale } from "../components/landing/AccessoLaterale";
import { Alert } from "../components/ui/Alert";
import { AuthLayout } from "../components/ui/AuthLayout";
import { Button } from "../components/ui/Button";
import { TextField } from "../components/ui/Field";
import { IconChip } from "../components/ui/IconChip";
import { TextLink } from "../components/ui/TextLink";
import { api, apiErrorMessage } from "../lib/api";

export default function RecuperaPassword() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setEmailError(null);
    if (!/^\S+@\S+\.\S+$/.test(email)) {
      setEmailError("Inserisci un indirizzo email valido.");
      return;
    }
    setLoading(true);
    try {
      // Il link parte dal backend con il nostro provider email (mai da Supabase).
      await api.post("/auth/recover", { email: email.trim() });
      setSent(true);
    } catch (err) {
      setError(apiErrorMessage(err, "Invio non riuscito, riprova tra qualche istante."));
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout laterale={<AccessoLaterale />}>
      {sent ? (
        // Risposta neutra: non dice se l'indirizzo è registrato.
        <div className="flex flex-col items-start gap-2" role="status">
          <IconChip icon={MailCheck} size="lg" className="mb-2" />
          <h1 className="text-title-page text-ink">Controlla la tua email</h1>
          <p className="text-body text-ink-2">
            Se <strong className="font-semibold text-ink">{email}</strong> è registrata su
            BandoFit, riceverai a breve un link per reimpostare la password. Controlla anche lo
            spam.
          </p>
          <TextLink to="/login" className="mt-4">
            Torna all'accesso
          </TextLink>
        </div>
      ) : (
        <>
          <h1 className="text-title-page text-ink">Password dimenticata?</h1>
          <p className="mt-1 text-body text-ink-2">
            Inserisci la tua email: ti invieremo un link per reimpostarla.
          </p>
          <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-4" noValidate>
            <TextField
              label="Email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="nome@azienda.it"
              error={emailError ?? undefined}
            />
            {error && <Alert tono="errore">{error}</Alert>}
            <Button type="submit" className="mt-2 w-full" size="lg" loading={loading}>
              Invia il link di recupero
            </Button>
          </form>
          <p className="mt-6 text-body text-ink-2">
            Te la ricordi? <TextLink to="/login">Accedi</TextLink>
          </p>
        </>
      )}
    </AuthLayout>
  );
}
