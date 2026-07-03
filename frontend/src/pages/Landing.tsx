import { ArrowRight, Bell, Filter, Search, ShieldCheck } from "lucide-react";
import { Link, Navigate } from "react-router-dom";
import { Logo } from "../components/layout/Logo";
import { PlanCard } from "../components/shared/PlanCard";
import { LinkButton } from "../components/ui/Button";
import { Skeleton } from "../components/ui/states";
import { useAuth } from "../hooks/useAuth";
import { usePlans } from "../hooks/usePlans";

const FEATURES = [
  {
    icon: Search,
    title: "Tutti i bandi in un posto solo",
    description:
      "Bandi europei, nazionali, regionali e locali raccolti e aggiornati di continuo: niente più decine di siti da monitorare.",
  },
  {
    icon: Filter,
    title: "Filtri pensati per le imprese",
    description:
      "Trova i bandi giusti per regione, settore, beneficiari, codici ATECO, importi e scadenze in pochi clic.",
  },
  {
    icon: Bell,
    title: "Mai più scadenze perse",
    description:
      "Con i piani a pagamento ricevi alert personalizzati con giorni di preavviso configurabili sui bandi che ti interessano.",
  },
  {
    icon: ShieldCheck,
    title: "Schede chiare e verificate",
    description:
      "Ogni bando ha una scheda leggibile: chi può candidarsi, quanto finanzia, come fare domanda e i link ufficiali.",
  },
];

export default function Landing() {
  const { session } = useAuth();
  const { data: plans, isPending: plansLoading, isError: plansError } = usePlans();

  if (session) return <Navigate to="/app/bandi" replace />;

  return (
    <div className="min-h-dvh bg-white">
      {/* Topbar */}
      <header className="border-b border-slate-100">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6">
          <Logo />
          <div className="flex items-center gap-2">
            <LinkButton to="/login" variant="ghost">
              Accedi
            </LinkButton>
            <LinkButton to="/registrati">Registrati</LinkButton>
          </div>
        </div>
      </header>

      {/* Hero */}
      <section className="bg-gradient-to-b from-brand-950 via-brand-900 to-brand-700 text-white">
        <div className="mx-auto max-w-7xl px-4 py-20 sm:px-6 sm:py-28">
          <div className="max-w-2xl">
            <span className="inline-flex items-center gap-2 rounded-full bg-white/10 px-3 py-1 text-xs font-medium text-brand-100 ring-1 ring-inset ring-white/20">
              <span className="size-1.5 animate-pulse rounded-full bg-emerald-400" aria-hidden />
              Oltre 1.200 bandi attivi monitorati
            </span>
            <h1 className="mt-5 font-display text-4xl font-bold leading-tight tracking-tight sm:text-5xl">
              Il radar sui bandi,
              <br />
              su misura per la tua impresa.
            </h1>
            <p className="mt-5 max-w-xl text-lg leading-relaxed text-brand-100">
              BandoFit raccoglie bandi europei, nazionali e regionali e te li presenta con
              filtri intelligenti, schede chiare e scadenze sempre sotto controllo.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <LinkButton
                to="/registrati"
                size="lg"
                className="bg-white text-brand-700 hover:bg-brand-50 active:bg-brand-100"
              >
                Inizia gratis
                <ArrowRight className="size-4" aria-hidden />
              </LinkButton>
              <LinkButton
                to="/login"
                size="lg"
                variant="secondary"
                className="border-white/30 bg-transparent text-white hover:border-white hover:bg-white/10 hover:text-white"
              >
                Ho già un account
              </LinkButton>
            </div>
          </div>
        </div>
      </section>

      {/* Feature */}
      <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20">
        <h2 className="text-center font-display text-3xl font-bold tracking-tight text-slate-900">
          Perché BandoFit
        </h2>
        <div className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {FEATURES.map((feature) => (
            <div
              key={feature.title}
              className="rounded-xl border border-slate-200 bg-white p-6 shadow-card transition-shadow hover:shadow-card-hover"
            >
              <div className="inline-flex rounded-lg bg-brand-50 p-2.5 text-brand-600">
                <feature.icon className="size-5" aria-hidden />
              </div>
              <h3 className="mt-4 font-display text-base font-semibold text-slate-900">
                {feature.title}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-slate-500">{feature.description}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Piani */}
      <section className="bg-surface">
        <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20">
          <h2 className="text-center font-display text-3xl font-bold tracking-tight text-slate-900">
            Un piano per ogni esigenza
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-center text-slate-500">
            Parti gratis ed esplora il catalogo. Passa a un piano superiore quando vuoi di più.
          </p>
          {plansLoading ? (
            <div className="mt-10 grid gap-6 pt-3 sm:grid-cols-2 lg:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-72 w-full" />
              ))}
            </div>
          ) : plansError ? (
            <p className="mt-10 text-center text-sm text-slate-500">
              Impossibile caricare i piani in questo momento.{" "}
              <Link to="/registrati" className="font-medium text-brand-600 hover:underline">
                Registrati
              </Link>{" "}
              per iniziare.
            </p>
          ) : (
            <div className="mt-10 grid gap-6 pt-3 sm:grid-cols-2 lg:grid-cols-4">
              {(plans ?? []).map((plan) => (
                <PlanCard
                  key={plan.id}
                  plan={plan}
                  highlighted={plan.slug === "pro"}
                  badge={plan.slug === "pro" ? "Consigliato" : undefined}
                  footer={
                    <LinkButton
                      to={`/registrati?piano=${plan.slug}`}
                      variant={plan.slug === "pro" ? "primary" : "secondary"}
                      className="w-full"
                    >
                      Scegli {plan.nome}
                    </LinkButton>
                  }
                />
              ))}
            </div>
          )}
        </div>
      </section>

      <footer className="border-t border-slate-100 bg-white">
        <div className="mx-auto flex max-w-7xl flex-col items-center gap-3 px-4 py-8 text-center sm:flex-row sm:justify-between sm:text-left">
          <Logo />
          <p className="text-sm text-slate-400">
            © {new Date().getFullYear()} BandoFit — La piattaforma per trovare i bandi giusti.
          </p>
        </div>
      </footer>
    </div>
  );
}
