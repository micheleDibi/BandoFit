import {
  ArrowRight,
  Building2,
  CalendarDays,
  Check,
  FileText,
  Globe,
  Layers,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Target,
} from "lucide-react";
import { Link, Navigate } from "react-router-dom";
import { Faq, type FaqItem } from "../components/landing/Faq";
import { FeatureCard } from "../components/landing/FeatureCard";
import { HeroShowcase } from "../components/landing/HeroShowcase";
import { SectionHeading } from "../components/landing/SectionHeading";
import { Logo } from "../components/layout/Logo";
import { PlanCard } from "../components/shared/PlanCard";
import { PoweredBy } from "../components/shared/PoweredBy";
import { LinkButton } from "../components/ui/Button";
import { Skeleton } from "../components/ui/states";
import { useAuth } from "../hooks/useAuth";
import { usePlans } from "../hooks/usePlans";
import { LANDING_COPY } from "../lib/copy";

const FEATURES = [
  {
    icon: Search,
    title: "Tutti i bandi in un posto solo",
    description:
      "Bandi europei, nazionali, regionali e locali raccolti e aggiornati di continuo: basta rincorrere decine di siti diversi.",
  },
  {
    icon: SlidersHorizontal,
    title: "Filtri pensati per le imprese",
    description:
      "Regione, settore, codici ATECO, beneficiari, importi e scadenze: restringi il campo ai bandi davvero adatti a te in pochi clic.",
  },
  {
    icon: Building2,
    title: "Dossier aziendale certificato",
    description:
      "Importa i dati della tua azienda dal Registro Imprese partendo dalla partita IVA: anagrafica, ATECO, sedi, cariche e dati economici. Ufficiali, non autodichiarati.",
  },
  {
    icon: Target,
    title: "Bandi per te",
    description:
      "Il profilo della tua azienda filtra i risultati: vedi prima i bandi in linea con la tua attività e con gli ambiti che segui.",
  },
  {
    icon: CalendarDays,
    title: "Scadenze sempre a fuoco",
    description:
      "Salva i bandi che ti interessano e porta le loro scadenze nel calendario, accanto ai tuoi appuntamenti. Nessuna occasione persa per una data dimenticata.",
  },
];

const STEPS = [
  {
    icon: Building2,
    title: "Crea la tua azienda",
    description: "Registrati e importa il dossier dal Registro Imprese partendo dalla partita IVA.",
  },
  {
    icon: Search,
    title: "Esplora i bandi",
    description: "Cerca e filtra nel catalogo, o lascia che «Bandi per te» faccia una prima selezione.",
  },
  {
    icon: Sparkles,
    title: "Verifica la compatibilità",
    description: "Lancia l'AI-check e leggi ammissibilità, punteggio e requisiti, punto per punto.",
  },
  {
    icon: CalendarDays,
    title: "Segui le scadenze",
    description: "Salva i bandi promettenti e tieni le loro scadenze sotto controllo nel calendario.",
  },
];

const STATS = [
  { value: LANDING_COPY.bandiValore, label: LANDING_COPY.bandiEtichetta },
  { value: "UE → locale", label: "Copertura su quattro livelli" },
  { value: "0–100", label: "Punteggio AI-check con citazioni" },
  { value: "Registro Imprese", label: "Dati aziendali certificati" },
];

const REASONS = [
  {
    icon: ShieldCheck,
    title: "Verdetti verificabili",
    description:
      "L'AI-check non si limita a dare un voto: ogni requisito è motivato con la citazione esatta presa dal testo del bando. Niente scatole nere.",
  },
  {
    icon: Building2,
    title: "Dati certificati, non a memoria",
    description:
      "Il profilo della tua azienda nasce dai dati ufficiali del Registro Imprese, così l'analisi parte da informazioni affidabili.",
  },
  {
    icon: Globe,
    title: "Copertura completa",
    description:
      "Bandi europei, nazionali, regionali e locali in un unico catalogo: una sola ricerca invece di decine di portali.",
  },
  {
    icon: Layers,
    title: "Pensato per le imprese italiane",
    description:
      "Filtri, profili e report parlano la lingua delle PMI: ATECO, beneficiari, classi dimensionali, account per l'azienda.",
  },
];

const FAQS: FaqItem[] = [
  {
    q: "Che cos'è BandoFit?",
    a: "Una piattaforma che raccoglie bandi e finanziamenti pubblici — europei, nazionali, regionali e locali — e ti aiuta a trovare quelli giusti per la tua azienda, con ricerca, filtri e un'analisi di compatibilità.",
  },
  {
    q: "I dati sulla mia azienda sono affidabili?",
    a: "Sì: puoi importare il dossier direttamente dal Registro Imprese partendo dalla partita IVA — anagrafica, codici ATECO, sedi, cariche e dati economici. Sono dati ufficiali, non autodichiarati.",
  },
  {
    q: "Come funziona l'AI-check?",
    a: "Analizza un bando rispetto al profilo della tua azienda e produce un report con l'esito di ammissibilità, un punteggio di compatibilità da 0 a 100 e i requisiti verificati uno per uno, ciascuno con la citazione esatta presa dal testo del bando.",
  },
  {
    q: "BandoFit è gratis?",
    a: "Puoi iniziare gratis ed esplorare il catalogo. I piani a pagamento aggiungono più analisi AI-check all'anno e altre funzioni: puoi cambiare piano quando vuoi.",
  },
  {
    q: "Quanti bandi trovo?",
    a: LANDING_COPY.bandiFaq,
  },
  {
    q: "Posso gestire più account per la mia azienda?",
    a: "Sì: con i piani adatti puoi collegare più persone o sedi della stessa azienda sotto un unico abbonamento, condividendo dati e quote.",
  },
];

const NAV_LINKS = [
  { href: "#funzionalita", label: "Funzionalità" },
  { href: "#come-funziona", label: "Come funziona" },
  { href: "#piani", label: "Piani" },
  { href: "#faq", label: "FAQ" },
];

export default function Landing() {
  const { session } = useAuth();
  const { data: plans, isPending: plansLoading, isError: plansError } = usePlans();

  if (session) return <Navigate to="/app/bandi" replace />;

  return (
    // overflow-x-clip: il bagliore decorativo dell'hero sporge oltre il
    // viewport su mobile; lo si contiene senza creare uno scroll-container
    // (l'header sticky resta ancorato al viewport).
    <div className="min-h-dvh overflow-x-clip bg-white">
      {/* Topbar */}
      <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-4 px-4 sm:px-6">
          <Logo />
          <nav className="hidden items-center gap-1 lg:flex" aria-label="Sezioni della pagina">
            {NAV_LINKS.map((link) => (
              <a
                key={link.href}
                href={link.href}
                className="rounded-lg px-3 py-2 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
              >
                {link.label}
              </a>
            ))}
          </nav>
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
        <div className="mx-auto grid max-w-7xl items-center gap-14 px-4 py-16 sm:px-6 sm:py-24 lg:grid-cols-2 lg:gap-10 lg:py-28">
          <div className="max-w-2xl">
            <span className="inline-flex items-center gap-2 rounded-full bg-white/10 px-3 py-1 text-xs font-medium text-brand-100 ring-1 ring-inset ring-white/20">
              <span className="size-1.5 animate-pulse rounded-full bg-emerald-400" aria-hidden />
              {LANDING_COPY.bandiClaim}
            </span>
            <h1 className="mt-5 font-display text-4xl font-bold leading-tight tracking-tight sm:text-5xl">
              Il radar sui bandi,
              <br />
              su misura per la tua impresa.
            </h1>
            <p className="mt-5 max-w-xl text-lg leading-relaxed text-brand-100">
              BandoFit raccoglie bandi europei, nazionali, regionali e locali e ti dice quali
              fanno per te — con filtri per impresa, schede chiare e un'analisi di compatibilità
              che cita il testo ufficiale.
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
            <ul className="mt-8 flex flex-wrap gap-x-6 gap-y-2 text-sm text-brand-100">
              <li className="inline-flex items-center gap-1.5">
                <Check className="size-4 text-emerald-400" aria-hidden />
                Bandi UE, nazionali, regionali e locali
              </li>
              <li className="inline-flex items-center gap-1.5">
                <Check className="size-4 text-emerald-400" aria-hidden />
                Dati dal Registro Imprese
              </li>
            </ul>
          </div>
          <div className="mb-14 lg:mb-0">
            <HeroShowcase />
          </div>
        </div>
      </section>

      {/* Problema → soluzione */}
      <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20">
        <SectionHeading
          eyebrow="Il problema"
          title="I bandi giusti esistono. Trovarli è il difficile."
          subtitle="Sono pubblicati su decine di portali diversi, con requisiti scritti in burocratese e scadenze facili da perdere. BandoFit li raccoglie, li rende leggibili e ti dice quali fanno per la tua azienda."
        />
        <div className="mt-10 grid gap-6 sm:grid-cols-3">
          {[
            {
              icon: Search,
              title: "Sparsi ovunque",
              description: "Un unico catalogo al posto di decine di siti da controllare a mano.",
            },
            {
              icon: FileText,
              title: "Requisiti oscuri",
              description: "Schede chiare e l'AI-check che spiega, citando il bando, se puoi partecipare.",
            },
            {
              icon: CalendarDays,
              title: "Scadenze che sfuggono",
              description: "Salvi i bandi e porti le scadenze nel calendario, sempre a portata d'occhio.",
            },
          ].map((item) => (
            <div key={item.title} className="rounded-xl border border-slate-200 bg-white p-6 shadow-card">
              <div className="inline-flex rounded-lg bg-brand-50 p-2.5 text-brand-600">
                <item.icon className="size-5" aria-hidden />
              </div>
              <h3 className="mt-4 font-display text-base font-semibold text-slate-900">{item.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-slate-600">{item.description}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Funzionalità */}
      <section id="funzionalita" className="scroll-mt-20 bg-surface">
        <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20">
          <SectionHeading
            eyebrow="Funzionalità"
            title="Tutto quello che serve per candidarti con criterio"
            subtitle="Dalla ricerca all'analisi di compatibilità: gli strumenti per passare dai «tanti bandi» ai «bandi giusti per te»."
          />
          <div className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            <FeatureCard
              icon={Sparkles}
              title="AI-check di compatibilità"
              description="Scopri se la tua azienda è ammissibile e quanto è compatibile con un punteggio da 0 a 100. Ogni requisito è verificato e motivato con la citazione esatta presa dal bando: un verdetto che puoi controllare, non un voto calato dall'alto."
              featured
            >
              <div className="flex flex-wrap gap-2">
                {["Esito di ammissibilità", "Punteggio 0–100", "Citazioni verificabili"].map((chip) => (
                  <span
                    key={chip}
                    className="inline-flex items-center gap-1 rounded-full bg-white px-2.5 py-1 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-200"
                  >
                    <Check className="size-3" aria-hidden />
                    {chip}
                  </span>
                ))}
              </div>
            </FeatureCard>
            {FEATURES.map((feature) => (
              <FeatureCard
                key={feature.title}
                icon={feature.icon}
                title={feature.title}
                description={feature.description}
              />
            ))}
          </div>
        </div>
      </section>

      {/* Come funziona */}
      <section id="come-funziona" className="scroll-mt-20">
        <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20">
          <SectionHeading
            eyebrow="Come funziona"
            title="Dai dati al bando giusto, in quattro passi"
          />
          <ol className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((step, i) => (
              <li
                key={step.title}
                className="relative rounded-xl border border-slate-200 bg-white p-6 shadow-card"
              >
                <div className="flex items-center gap-3">
                  <span className="tabular inline-flex size-8 items-center justify-center rounded-full bg-brand-500 font-display text-sm font-bold text-white">
                    {i + 1}
                  </span>
                  <step.icon className="size-5 text-brand-600" aria-hidden />
                </div>
                <h3 className="mt-4 font-display text-base font-semibold text-slate-900">
                  {step.title}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-slate-600">{step.description}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* Perché sceglierci + numeri reali */}
      <section className="bg-surface">
        <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20">
          <SectionHeading
            eyebrow="Perché BandoFit"
            title="Uno strumento serio, non l'ennesima lista di bandi"
          />
          <div className="mt-10 grid gap-6 sm:grid-cols-2">
            {REASONS.map((reason) => (
              <div key={reason.title} className="flex gap-4 rounded-xl border border-slate-200 bg-white p-6 shadow-card">
                <div className="inline-flex h-fit rounded-lg bg-brand-50 p-2.5 text-brand-600">
                  <reason.icon className="size-5" aria-hidden />
                </div>
                <div>
                  <h3 className="font-display text-base font-semibold text-slate-900">{reason.title}</h3>
                  <p className="mt-1 text-sm leading-relaxed text-slate-600">{reason.description}</p>
                </div>
              </div>
            ))}
          </div>
          <dl className="mt-8 grid gap-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-card sm:grid-cols-2 lg:grid-cols-4">
            {STATS.map((stat) => (
              <div key={stat.label} className="text-center">
                <dt className="font-display text-2xl font-bold text-brand-600">{stat.value}</dt>
                <dd className="mt-1 text-sm text-slate-600">{stat.label}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      {/* Piani */}
      <section id="piani" className="scroll-mt-20">
        <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20">
          <SectionHeading
            eyebrow="Piani"
            title="Un piano per ogni esigenza"
            subtitle="Parti gratis ed esplora il catalogo. Passa a un piano superiore quando vuoi più analisi AI-check e più funzioni."
          />
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
                    plan.tipo_prezzo === "su_richiesta" ? (
                      // Non selezionabile self-serve: niente deep-link ?piano=
                      // (in Register verrebbe scartato), si entra e si richiede
                      // dall'app.
                      <LinkButton to="/registrati" variant="secondary" className="w-full">
                        Richiedi una consulenza
                      </LinkButton>
                    ) : (
                      <LinkButton
                        to={`/registrati?piano=${plan.slug}`}
                        variant={plan.slug === "pro" ? "primary" : "secondary"}
                        className="w-full"
                      >
                        Scegli {plan.nome}
                      </LinkButton>
                    )
                  }
                />
              ))}
            </div>
          )}
        </div>
      </section>

      {/* FAQ */}
      <section id="faq" className="scroll-mt-20 bg-surface">
        <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20">
          <SectionHeading eyebrow="FAQ" title="Domande frequenti" />
          <Faq items={FAQS} />
        </div>
      </section>

      {/* CTA finale */}
      <section className="bg-gradient-to-br from-brand-800 to-brand-600 text-white">
        <div className="mx-auto max-w-7xl px-4 py-16 text-center sm:px-6 sm:py-20">
          <h2 className="font-display text-3xl font-bold tracking-tight">
            Pronto a trovare i bandi giusti per la tua azienda?
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-brand-100">
            Crea il tuo account gratuito ed esplora subito il catalogo. Nessuna carta richiesta.
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
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
              Accedi
            </LinkButton>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-slate-200 bg-white">
        <div className="mx-auto max-w-7xl px-4 py-12 sm:px-6">
          <div className="flex flex-col gap-10 sm:flex-row sm:justify-between">
            <div className="max-w-sm">
              <Logo className="h-10" />
              <p className="mt-4 text-sm text-slate-500">
                La piattaforma per trovare i bandi giusti per la tua azienda: europei, nazionali,
                regionali e locali.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-10 sm:gap-16">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Prodotto</p>
                <ul className="mt-3 space-y-2 text-sm">
                  {NAV_LINKS.map((link) => (
                    <li key={link.href}>
                      <a href={link.href} className="text-slate-600 hover:text-brand-600">
                        {link.label}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Account</p>
                <ul className="mt-3 space-y-2 text-sm">
                  <li>
                    <Link to="/login" className="text-slate-600 hover:text-brand-600">
                      Accedi
                    </Link>
                  </li>
                  <li>
                    <Link to="/registrati" className="text-slate-600 hover:text-brand-600">
                      Registrati
                    </Link>
                  </li>
                </ul>
              </div>
            </div>
          </div>
          <div className="mt-10 flex flex-col items-center justify-between gap-4 border-t border-slate-100 pt-6 sm:flex-row">
            <p className="text-sm text-slate-500">
              © {new Date().getFullYear()} BandoFit — La piattaforma per trovare i bandi giusti.
            </p>
            <PoweredBy />
          </div>
        </div>
      </footer>
    </div>
  );
}
