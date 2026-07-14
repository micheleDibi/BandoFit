import { ArrowLeft, Eye, EyeOff } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Logo } from "../components/layout/Logo";
import { PlanCard } from "../components/shared/PlanCard";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Combobox } from "../components/ui/Combobox";
import { TextField } from "../components/ui/Field";
import { ErrorState, Skeleton } from "../components/ui/states";
import { useJobPositions } from "../hooks/useJobPositions";
import { usePlans } from "../hooks/usePlans";
import { api, apiErrorMessage } from "../lib/api";
import { cn } from "../lib/cn";
import { supabase } from "../lib/supabase";
import { isValidTelefono, normalizeTelefono } from "../lib/telefono";

interface FormData {
  nome: string;
  cognome: string;
  azienda: string;
  telefono: string;
  email: string;
  password: string;
  confirm: string;
}

const EMPTY_FORM: FormData = {
  nome: "",
  cognome: "",
  azienda: "",
  telefono: "",
  email: "",
  password: "",
  confirm: "",
};

type FieldErrors = Partial<Record<keyof FormData | "posizione", string>>;

function StepIndicator({ step }: { step: 1 | 2 }) {
  return (
    <div className="flex items-center gap-2">
      <span className="sr-only" role="status">
        Passo {step} di 2
      </span>
      {[1, 2].map((s) => (
        <span
          key={s}
          className={cn(
            "h-1.5 rounded-full transition-all duration-300",
            s === step ? "w-8 bg-brand-500" : "w-4 bg-slate-200",
            s < step && "bg-brand-300",
          )}
          aria-hidden
        />
      ))}
    </div>
  );
}

export default function Register() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { data: plans, isPending: plansLoading, isError: plansError, refetch } = usePlans();
  const {
    data: positions,
    isError: positionsError,
    refetch: refetchPositions,
  } = useJobPositions();

  const [step, setStep] = useState<1 | 2>(1);
  const [form, setForm] = useState<FormData>(EMPTY_FORM);
  const [positionId, setPositionId] = useState<number | null>(null);
  const [posizioneAltro, setPosizioneAltro] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [showPassword, setShowPassword] = useState(false);
  const [selectedPlan, setSelectedPlan] = useState<string>(searchParams.get("piano") ?? "gratuito");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Se lo slug ?piano non corrisponde ad alcun piano attivo selezionabile
  // (i piani «su richiesta» non si scelgono alla registrazione), ripiega su
  // gratuito o sul primo piano self-serve.
  useEffect(() => {
    if (!plans || plans.length === 0) return;
    const selfServe = plans.filter((p) => p.tipo_prezzo !== "su_richiesta");
    if (!selfServe.some((p) => p.slug === selectedPlan)) {
      setSelectedPlan(
        selfServe.some((p) => p.slug === "gratuito") ? "gratuito" : selfServe[0]?.slug ?? "",
      );
    }
  }, [plans, selectedPlan]);

  const set = (key: keyof FormData) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  const selectedPosition = positions?.find((p) => p.id === positionId) ?? null;

  const validateStep1 = (): boolean => {
    const errors: FieldErrors = {};
    if (!form.nome.trim()) errors.nome = "Il nome è obbligatorio.";
    if (!form.cognome.trim()) errors.cognome = "Il cognome è obbligatorio.";
    if (!form.telefono.trim()) {
      errors.telefono = "Il numero di telefono è obbligatorio.";
    } else if (!isValidTelefono(normalizeTelefono(form.telefono))) {
      errors.telefono = "Inserisci un numero di telefono valido (es. 347 1234567).";
    }
    if (positionId === null) errors.posizione = "Seleziona la tua posizione in azienda.";
    if (!/^\S+@\S+\.\S+$/.test(form.email)) errors.email = "Inserisci un indirizzo email valido.";
    if (form.password.length < 8) errors.password = "La password deve avere almeno 8 caratteri.";
    if (form.confirm !== form.password) errors.confirm = "Le password non coincidono.";
    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleStep1 = (e: FormEvent) => {
    e.preventDefault();
    if (validateStep1()) setStep(2);
  };

  const handleSubmit = async () => {
    setError(null);
    setInfo(null);
    setLoading(true);
    try {
      // La registrazione passa dal backend: l'email di conferma parte dal
      // NOSTRO provider (SMTP/OVH), mai dal mailer di Supabase.
      const { data } = await api.post<{ confirmation_required: boolean }>("/auth/register", {
        email: form.email.trim(),
        password: form.password,
        nome: form.nome.trim(),
        cognome: form.cognome.trim(),
        azienda: form.azienda.trim() || null,
        telefono: normalizeTelefono(form.telefono),
        job_position_slug: selectedPosition?.slug ?? "",
        job_position_altro:
          selectedPosition?.slug === "altro" ? posizioneAltro.trim() || null : null,
        plan_slug: selectedPlan,
      });
      if (data.confirmation_required) {
        setLoading(false);
        setInfo(
          "Ti abbiamo inviato una email di conferma: aprila per attivare l'account, poi accedi.",
        );
        return;
      }
      // Conferma email disattivata sul progetto: accesso immediato.
      const { error: signInError } = await supabase.auth.signInWithPassword({
        email: form.email.trim(),
        password: form.password,
      });
      setLoading(false);
      if (signInError) {
        navigate("/login", { replace: true });
        return;
      }
      navigate("/app/bandi", { replace: true });
    } catch (err) {
      setLoading(false);
      setError(apiErrorMessage(err, "Registrazione non riuscita. Riprova tra qualche istante."));
    }
  };

  return (
    <div className="flex min-h-dvh flex-col items-center bg-surface px-4 py-10">
      <Link to="/" className="mb-8 rounded-lg focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-brand-500">
        <Logo variant="vertical" />
      </Link>

      {step === 1 ? (
        <Card className="w-full max-w-md p-6 sm:p-8">
          <div className="flex items-center justify-between">
            <h1 className="font-display text-xl font-bold text-slate-900">Crea il tuo account</h1>
            <StepIndicator step={1} />
          </div>
          <p className="mt-1 text-sm text-slate-500">I tuoi dati, poi scegli il piano.</p>

          <form onSubmit={handleStep1} className="mt-6 space-y-4" noValidate>
            <div className="grid grid-cols-2 gap-3">
              <TextField
                label="Nome"
                required
                autoComplete="given-name"
                value={form.nome}
                onChange={set("nome")}
                error={fieldErrors.nome}
              />
              <TextField
                label="Cognome"
                required
                autoComplete="family-name"
                value={form.cognome}
                onChange={set("cognome")}
                error={fieldErrors.cognome}
              />
            </div>
            <TextField
              label="Azienda"
              autoComplete="organization"
              value={form.azienda}
              onChange={set("azienda")}
              helper="Facoltativa"
            />
            <TextField
              label="Telefono"
              type="tel"
              required
              autoComplete="tel"
              value={form.telefono}
              onChange={set("telefono")}
              error={fieldErrors.telefono}
              helper={
                !fieldErrors.telefono ? "Es. 347 1234567 — prefisso +39 automatico" : undefined
              }
            />
            <Combobox
              label="Posizione in azienda"
              required
              options={(positions ?? []).map((p) => ({ id: p.id, label: p.nome }))}
              value={positionId}
              onChange={setPositionId}
              placeholder="Cerca la tua posizione…"
              disabled={!positions}
              error={fieldErrors.posizione}
            />
            {positionsError && (
              <p className="text-sm text-red-600" role="alert">
                Impossibile caricare le posizioni.{" "}
                <button
                  type="button"
                  onClick={() => refetchPositions()}
                  className="cursor-pointer font-medium underline underline-offset-2"
                >
                  Riprova
                </button>
              </p>
            )}
            {selectedPosition?.slug === "altro" && (
              <TextField
                label="Specifica la posizione"
                value={posizioneAltro}
                onChange={(e) => setPosizioneAltro(e.target.value)}
                helper="Facoltativa"
                maxLength={100}
              />
            )}
            <TextField
              label="Email"
              type="email"
              required
              autoComplete="email"
              value={form.email}
              onChange={set("email")}
              error={fieldErrors.email}
              placeholder="nome@azienda.it"
            />
            <div className="relative">
              <TextField
                label="Password"
                type={showPassword ? "text" : "password"}
                required
                autoComplete="new-password"
                value={form.password}
                onChange={set("password")}
                error={fieldErrors.password}
                helper={!fieldErrors.password ? "Almeno 8 caratteri" : undefined}
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
            <TextField
              label="Conferma password"
              type={showPassword ? "text" : "password"}
              required
              autoComplete="new-password"
              value={form.confirm}
              onChange={set("confirm")}
              error={fieldErrors.confirm}
            />

            <Button type="submit" className="w-full" size="lg">
              Continua
            </Button>
          </form>

          <p className="mt-6 text-center text-sm text-slate-500">
            Hai già un account?{" "}
            <Link to="/login" className="font-medium text-brand-600 underline-offset-2 hover:underline">
              Accedi
            </Link>
          </p>
        </Card>
      ) : (
        <div className="w-full max-w-4xl">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="font-display text-xl font-bold text-slate-900">Scegli il tuo piano</h1>
              <p className="mt-1 text-sm text-slate-500">
                Abbonamento annuale, puoi cambiarlo in qualsiasi momento dalla pagina Abbonamento.
              </p>
            </div>
            <StepIndicator step={2} />
          </div>

          {plansLoading ? (
            <div className="mt-8 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-72 w-full" />
              ))}
            </div>
          ) : plansError ? (
            <div className="mt-8">
              <ErrorState
                message="Impossibile caricare i piani. Riprova."
                onRetry={() => refetch()}
              />
            </div>
          ) : (
            <div className="mt-8 grid gap-5 pt-3 sm:grid-cols-2 lg:grid-cols-4">
              {(plans ?? []).map((plan) =>
                plan.tipo_prezzo === "su_richiesta" ? (
                  // Visibile ma non selezionabile: senza onClick la card è un
                  // div non interattivo (il backend rifiuta comunque il piano
                  // alla registrazione).
                  <PlanCard
                    key={plan.id}
                    plan={plan}
                    footer={
                      <p className="text-xs text-slate-500">
                        Disponibile su richiesta: contattaci dalla pagina Abbonamento dopo la
                        registrazione.
                      </p>
                    }
                  />
                ) : (
                  <PlanCard
                    key={plan.id}
                    plan={plan}
                    selected={selectedPlan === plan.slug}
                    highlighted={plan.slug === "pro"}
                    badge={plan.slug === "pro" ? "Consigliato" : undefined}
                    onClick={() => setSelectedPlan(plan.slug)}
                  />
                ),
              )}
            </div>
          )}

          {error && (
            <p className="mt-5 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">
              {error}
            </p>
          )}
          {info && (
            <div className="mt-5 rounded-lg bg-brand-50 px-4 py-3 text-sm text-brand-800" role="status">
              {info}{" "}
              <Link to="/login" className="font-medium underline underline-offset-2">
                Vai al login
              </Link>
            </div>
          )}

          <div className="mt-8 flex items-center justify-between">
            <Button variant="ghost" onClick={() => setStep(1)}>
              <ArrowLeft className="size-4" aria-hidden />
              Indietro
            </Button>
            <Button
              size="lg"
              onClick={handleSubmit}
              loading={loading}
              disabled={plansLoading || plansError || !!info}
            >
              Crea account con piano{" "}
              {plans?.find((p) => p.slug === selectedPlan)?.nome ?? selectedPlan}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
