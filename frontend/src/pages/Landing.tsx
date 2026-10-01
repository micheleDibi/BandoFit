import {
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
  type LucideIcon,
} from "lucide-react";
import { Navigate } from "react-router-dom";
import { RigheEsempio } from "../components/landing/RigheEsempio";
import { Contenitore, Sezione } from "../components/landing/Sezione";
import { Logo } from "../components/layout/Logo";
import { PlanCard } from "../components/shared/PlanCard";
import { PoweredBy } from "../components/shared/PoweredBy";
import { Accordion } from "../components/ui/Accordion";
import { LinkButton } from "../components/ui/Button";
import { Facts } from "../components/ui/Facts";
import { Panel } from "../components/ui/Panel";
import { ErrorState, Skeleton } from "../components/ui/states";
import { TextLink } from "../components/ui/TextLink";
import { useAuth } from "../hooks/useAuth";
import { usePlans } from "../hooks/usePlans";
import { LANDING_COPY } from "../lib/copy";

interface Voce {
  icon: LucideIcon;
  title: string;
  description: string;
}

const PROBLEMI: Voce[] = [
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
];

const AI_CHECK_PUNTI = ["Esito di ammissibilità", "Punteggio 0–100", "Citazioni verificabili"];

const FEATURES: Voce[] = [
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
    title: "Crea la tua azienda",
    description: "Registrati e importa il dossier dal Registro Imprese partendo dalla partita IVA.",
  },
  {
    title: "Esplora i bandi",
    description: "Cerca e filtra nel catalogo, o lascia che «Bandi per te» faccia una prima selezione.",
  },
  {
    title: "Lancia l'AI-check",
    description: "Leggi ammissibilità, punteggio e requisiti, punto per punto.",
  },
  {
    title: "Segui le scadenze",
    description: "Salva i bandi promettenti e tieni le loro scadenze sotto controllo nel calendario.",
  },
];

const STATS = [
  { valore: LANDING_COPY.bandiValore, etichetta: LANDING_COPY.bandiEtichetta },
  { valore: "4 livelli", etichetta: "Copertura: europea, nazionale, regionale e locale" },
  { valore: "0–100", etichetta: "Punteggio AI-check con citazioni" },
  { valore: "Registro Imprese", etichetta: "Dati aziendali certificati" },
];

const REASONS: Voce[] = [
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

const FAQS = [
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

/** Il PNG del logo orizzontale ha un margine trasparente del 25% sopra e sotto e
 *  del 14% ai lati: a 64px d'altezza il marchio visibile è alto 32px, e i
 *  margini negativi riportano il riquadro sul segno. Nella testata sotto `sm`
 *  48px (marchio 24px), così logo e pulsanti stanno in 390px. */
const LOGO_TESTATA = "-mx-4.5 -my-3 h-12 sm:-mx-6 sm:-my-4 sm:h-16";
const LOGO_PIEDE = "-mx-6 -my-4 h-16";

const NAV_LINKS = [
  { href: "#funzionalita", label: "Funzionalità" },
  { href: "#come-funziona", label: "Come funziona" },
  { href: "#piani", label: "Piani" },
  { href: "#faq", label: "FAQ" },
];

/** Una voce a filetto (niente card): icona, titolo, testo. */
function VoceFiletto({ icon: Icon, title, description }: Voce) {
  return (
    <div className="flex flex-col gap-2 border-t border-line pt-5">
      <Icon className="size-5 text-accent" strokeWidth={1.75} aria-hidden />
      <h3 className="font-sans text-row-title text-ink">{title}</h3>
      <p className="text-body text-ink-2">{description}</p>
    </div>
  );
}

export default function Landing() {
  const { session } = useAuth();
  const {
    data: plans,
    isPending: plansLoading,
    isError: plansError,
    refetch: refetchPlans,
  } = usePlans();

  if (session) return <Navigate to="/app" replace />;

  return (
    <div className="min-h-dvh bg-sheet">
      {/* Testata: l'unico pulsante pieno della prima schermata è quello dell'hero. */}
      <header className="sticky top-0 z-40 border-b border-line bg-sheet">
        <Contenitore className="flex h-16 items-center justify-between gap-4">
          <Logo className={LOGO_TESTATA} />
          <nav className="hidden items-center gap-1 lg:flex" aria-label="Sezioni della pagina">
            {NAV_LINKS.map((link) => (
              <a
                key={link.href}
                href={link.href}
                className="rounded-control px-3 py-2 text-body font-medium text-ink-2 transition-colors hover:bg-sunken hover:text-ink"
              >
                {link.label}
              </a>
            ))}
          </nav>
          <div className="flex items-center gap-2">
            <LinkButton to="/login" variant="ghost" size="sm">
              Accedi
            </LinkButton>
            <LinkButton to="/registrati" variant="secondary" size="sm">
              Registrati
            </LinkButton>
          </div>
        </Contenitore>
      </header>

      <main>
        {/* Hero */}
        <section aria-labelledby="hero-titolo">
          <Contenitore className="grid items-center gap-12 py-16 sm:py-20 lg:grid-cols-2 lg:gap-16 lg:py-24">
            <div className="flex flex-col items-start gap-6">
              <h1 id="hero-titolo" className="text-title-hero text-ink">
                Il radar sui bandi,
                <br />
                su misura per la tua impresa.
              </h1>
              <p className="max-w-lettura text-prose text-ink-2">
                BandoFit raccoglie bandi europei, nazionali, regionali e locali e ti dice quali
                fanno per te — con filtri per impresa, schede chiare e un'analisi di compatibilità
                che cita il testo ufficiale.
              </p>
              <div className="flex flex-wrap gap-3">
                <LinkButton to="/registrati" size="lg">
                  Inizia gratis
                </LinkButton>
                <LinkButton to="/login" size="lg" variant="secondary">
                  Ho già un account
                </LinkButton>
              </div>
              <ul className="flex flex-col gap-2 text-body text-ink-2">
                {[
                  LANDING_COPY.bandiClaim,
                  "Bandi UE, nazionali, regionali e locali",
                  "Dati dal Registro Imprese",
                ].map((punto) => (
                  <li key={punto} className="flex items-start gap-2">
                    <Check className="mt-0.75 size-4 shrink-0 text-accent" aria-hidden />
                    {punto}
                  </li>
                ))}
              </ul>
            </div>
            <Panel aria-label="Esempio" className="gap-4 p-6 sm:p-8">
              <p className="text-small text-ink-3">
                Esempio: scadenza, valore e compatibilità di ogni bando, in una riga.
              </p>
              <RigheEsempio />
            </Panel>
          </Contenitore>
        </section>

        <Sezione
          fondo="desk"
          titolo="I bandi giusti esistono. Trovarli è il difficile."
          sottotitolo="Sono pubblicati su decine di portali diversi, con requisiti scritti in burocratese e scadenze facili da perdere. BandoFit li raccoglie, li rende leggibili e ti dice quali fanno per la tua azienda."
        >
          <div className="grid gap-8 sm:grid-cols-3 sm:gap-6">
            {PROBLEMI.map((voce) => (
              <VoceFiletto key={voce.title} {...voce} />
            ))}
          </div>
        </Sezione>

        <Sezione
          id="funzionalita"
          titolo="Tutto quello che serve per candidarti con criterio"
          sottotitolo="Dalla ricerca all'analisi di compatibilità: gli strumenti per passare dai «tanti bandi» ai «bandi giusti per te»."
        >
          <Panel
            aria-label="AI-check"
            className="grid gap-6 p-6 sm:p-8 lg:grid-cols-2 lg:gap-12"
          >
            <div className="flex flex-col gap-2">
              <Sparkles className="size-5 text-accent" strokeWidth={1.75} aria-hidden />
              <h3 className="text-title-section text-ink">AI-check</h3>
              <p className="text-body text-ink-2">
                Scopri se la tua azienda è ammissibile e quanto è compatibile con un punteggio da 0
                a 100. Ogni requisito è verificato e motivato con la citazione esatta presa dal
                bando: un verdetto che puoi controllare, non un voto calato dall'alto.
              </p>
            </div>
            <ul className="flex flex-col gap-3 self-center">
              {AI_CHECK_PUNTI.map((punto) => (
                <li key={punto} className="flex items-start gap-2 text-title-group text-ink">
                  <Check className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden />
                  {punto}
                </li>
              ))}
            </ul>
          </Panel>
          <div className="grid gap-8 sm:grid-cols-2 sm:gap-6 lg:grid-cols-3">
            {FEATURES.map((voce) => (
              <VoceFiletto key={voce.title} {...voce} />
            ))}
          </div>
        </Sezione>

        <Sezione
          id="come-funziona"
          fondo="desk"
          titolo="Dai dati al bando giusto, in quattro passi"
        >
          <ol className="grid gap-8 sm:grid-cols-2 sm:gap-6 lg:grid-cols-4">
            {STEPS.map((step, i) => (
              <li key={step.title} className="flex flex-col gap-2">
                <span className="mb-3 h-1 rounded-pill bg-accent" aria-hidden />
                <span className="text-figure-sm text-accent">{i + 1}</span>
                <h3 className="font-sans text-row-title text-ink">{step.title}</h3>
                <p className="text-body text-ink-2">{step.description}</p>
              </li>
            ))}
          </ol>
        </Sezione>

        <Sezione titolo="Uno strumento serio, non l'ennesima lista di bandi">
          <div className="grid gap-8 sm:grid-cols-2 sm:gap-6">
            {REASONS.map((voce) => (
              <VoceFiletto key={voce.title} {...voce} />
            ))}
          </div>
          <Facts items={STATS} />
        </Sezione>

        <Sezione
          id="piani"
          fondo="desk"
          titolo="Un piano per ogni esigenza"
          sottotitolo="Parti gratis ed esplora il catalogo. Passa a un piano superiore quando vuoi più analisi AI-check e più funzioni."
        >
          {plansLoading ? (
            <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-72 w-full" />
              ))}
            </div>
          ) : plansError ? (
            <div className="flex flex-col items-start gap-2">
              <ErrorState
                title="Impossibile caricare i piani in questo momento."
                onRetry={() => refetchPlans()}
              />
              <p className="text-body text-ink-2">
                Puoi comunque <TextLink to="/registrati">registrarti</TextLink> per iniziare.
              </p>
            </div>
          ) : (
            <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
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
        </Sezione>

        <Sezione id="faq" titolo="Domande frequenti">
          <Accordion
            className="max-w-lettura"
            items={FAQS.map((faq, i) => ({
              id: `faq-${i + 1}`,
              titolo: faq.q,
              children: <p>{faq.a}</p>,
            }))}
          />
        </Sezione>

        {/* Invito finale: sul piano, senza gradiente. */}
        <section aria-labelledby="invito-titolo" className="border-t border-line bg-desk">
          <Contenitore className="flex flex-col items-start gap-4 py-16 sm:py-20">
            <h2 id="invito-titolo" className="text-title-page text-ink">
              Pronto a trovare i bandi giusti per la tua azienda?
            </h2>
            <p className="max-w-lettura text-prose text-ink-2">
              Crea il tuo account gratuito ed esplora subito il catalogo. Nessuna carta richiesta.
            </p>
            <div className="mt-2 flex flex-wrap gap-3">
              <LinkButton to="/registrati" size="lg">
                Inizia gratis
              </LinkButton>
              <LinkButton to="/login" size="lg" variant="secondary">
                Accedi
              </LinkButton>
            </div>
          </Contenitore>
        </section>
      </main>

      <footer className="border-t border-line">
        <Contenitore className="py-12">
          <div className="flex flex-col gap-10 sm:flex-row sm:justify-between">
            <div className="flex max-w-lettura flex-col gap-4">
              <Logo className={LOGO_PIEDE} />
              <p className="text-body text-ink-2">
                La piattaforma per trovare i bandi giusti per la tua azienda: europei, nazionali,
                regionali e locali.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-10 sm:gap-16">
              <nav aria-labelledby="piede-prodotto" className="flex flex-col gap-3">
                <h2 id="piede-prodotto" className="font-sans text-title-group text-ink">
                  Prodotto
                </h2>
                <ul className="flex flex-col gap-2">
                  {NAV_LINKS.map((link) => (
                    <li key={link.href}>
                      <TextLink href={link.href} className="text-ink-2 hover:text-ink">
                        {link.label}
                      </TextLink>
                    </li>
                  ))}
                </ul>
              </nav>
              <nav aria-labelledby="piede-account" className="flex flex-col gap-3">
                <h2 id="piede-account" className="font-sans text-title-group text-ink">
                  Account
                </h2>
                <ul className="flex flex-col gap-2">
                  <li>
                    <TextLink to="/login" className="text-ink-2 hover:text-ink">
                      Accedi
                    </TextLink>
                  </li>
                  <li>
                    <TextLink to="/registrati" className="text-ink-2 hover:text-ink">
                      Registrati
                    </TextLink>
                  </li>
                </ul>
              </nav>
            </div>
          </div>
          {/* `PoweredBy` anche se il logo lo dice già: è l'unico link a edunews24.it. */}
          <div className="mt-10 flex flex-col items-start justify-between gap-4 border-t border-line pt-6 sm:flex-row sm:items-center">
            <p className="text-small text-ink-3">
              © {new Date().getFullYear()} BandoFit — La piattaforma per trovare i bandi giusti.
            </p>
            <PoweredBy />
          </div>
        </Contenitore>
      </footer>
    </div>
  );
}
