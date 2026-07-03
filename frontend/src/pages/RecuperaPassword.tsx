import { MailCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Logo } from "../components/layout/Logo";
import { PoweredBy } from "../components/shared/PoweredBy";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { TextField } from "../components/ui/Field";
import { api, apiErrorMessage } from "../lib/api";

export default function RecuperaPassword() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!/^\S+@\S+\.\S+$/.test(email)) {
      setError("Inserisci un indirizzo email valido.");
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
    <div className="flex min-h-dvh flex-col items-center justify-center bg-surface px-4 py-10">
      <Link
        to="/"
        className="mb-8 rounded-lg focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-brand-500"
      >
        <Logo variant="vertical" />
      </Link>
      <Card className="w-full max-w-md p-6 sm:p-8">
        {sent ? (
          <div className="flex flex-col items-center py-4 text-center" role="status">
            <div className="rounded-full bg-brand-50 p-3 text-brand-600">
              <MailCheck className="size-7" aria-hidden />
            </div>
            <h1 className="mt-4 font-display text-lg font-bold text-slate-900">
              Controlla la tua email
            </h1>
            <p className="mt-2 text-sm text-slate-500">
              Se <strong className="text-slate-700">{email}</strong> è registrata su BandoFit,
              riceverai a breve un link per reimpostare la password. Controlla anche lo spam.
            </p>
            <Link
              to="/login"
              className="mt-6 text-sm font-medium text-brand-600 underline-offset-2 hover:underline"
            >
              Torna all'accesso
            </Link>
          </div>
        ) : (
          <>
            <h1 className="font-display text-xl font-bold text-slate-900">Password dimenticata?</h1>
            <p className="mt-1 text-sm text-slate-500">
              Inserisci la tua email: ti invieremo un link per reimpostarla.
            </p>
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
              {error && (
                <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
                  {error}
                </p>
              )}
              <Button type="submit" className="w-full" size="lg" loading={loading}>
                Invia il link di recupero
              </Button>
            </form>
            <p className="mt-6 text-center text-sm text-slate-500">
              Te la ricordi?{" "}
              <Link
                to="/login"
                className="font-medium text-brand-600 underline-offset-2 hover:underline"
              >
                Accedi
              </Link>
            </p>
          </>
        )}
      </Card>
      <PoweredBy className="mt-8" />
    </div>
  );
}
