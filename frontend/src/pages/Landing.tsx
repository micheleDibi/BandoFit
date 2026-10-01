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
import { Contenitore, SegnoTitolo, Sezione } from "../components/landing/Sezione";
import { Logo } from "../components/layout/Logo";
import { GHOST_SU_FASCIA } from "../components/shared/fascia";
import { PlanCard } from "../components/shared/PlanCard";
import { PoweredBy } from "../components/shared/PoweredBy";
import { Accordion } from "../components/ui/Accordion";
import { areaClassi, type Area } from "../components/ui/area";
import { LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { IconChip } from "../components/ui/IconChip";
import { KpiCard } from "../components/ui/KpiCard";
import { Panel } from "../components/ui/Panel";
import { ErrorState, Skeleton } from "../components/ui/states";
import { TextLink } from "../components/ui/TextLink";
import { useAuth } from "../hooks/useAuth";
import { usePlans } from "../hooks/usePlans";
import { cn } from "../lib/cn";
import { LANDING_COPY } from "../lib/copy";

interface Voce {
  icon: LucideIcon;
  title: string;
  description: string;
  /** Il colore dell'`IconChip`: l'area dell'app di cui parla la voce. */
  area: Area;
}

const PROBLEMI: Voce[] = [
  {
    icon: Search,
    title: "Sparsi ovunque",
    description: "Un unico catalogo al posto di decine di siti da controllare a mano.",
    area: "bandi",
  },
  {
    icon: FileText,
    title: "Requisiti oscuri",
    description: "Schede chiare e l'AI-check che spiega, citando il bando, se puoi partecipare.",
    area: "aicheck",
  },
  {
    icon: CalendarDays,
    title: "Scadenze che sfuggono",
    description: "Salvi i bandi e porti le scadenze nel calendario, sempre a portata d'occhio.",
    area: "scadenze",
  },
];

const AI_CHECK_PUNTI = ["Esito di ammissibilità", "Punteggio 0–100", "Citazioni verificabili"];

const FEATURES: Voce[] = [
  {
    icon: Search,
    title: "Tutti i bandi in un posto solo",
    description:
      "Bandi europei, nazionali, regionali e locali raccolti e aggiornati di continuo: basta rincorrere decine di siti diversi.",
    area: "bandi",
  },
  {
    icon: SlidersHorizontal,
    title: "Filtri pensati per le imprese",
    description:
      "Regione, settore, codici ATECO, beneficiari, importi e scadenze: restringi il campo ai bandi davvero adatti a te in pochi clic.",
    area: "bandi",
  },
  {
    icon: Building2,
    title: "Dossier aziendale certificato",
    description:
      "Importa i dati della tua azienda dal Registro Imprese partendo dalla partita IVA: anagrafica, ATECO, sedi, cariche e dati economici. Ufficiali, non autodichiarati.",
    area: "azienda",
  },
  {
    icon: Target,
    title: "Bandi per te",
    description:
      "Il profilo della tua azienda filtra i risultati: vedi prima i bandi in linea con la tua attività e con gli ambiti che segui.",
    area: "home",
  },
  {
    icon: CalendarDays,
    title: "Scadenze sempre a fuoco",
    description:
      "Salva i bandi che ti interessano e porta le loro scadenze nel calendario, accanto ai tuoi appuntamenti. Nessuna occasione persa per una data dimenticata.",
    area: "scadenze",
  },
];

// Ogni passo ha il numero nel colore dell'area di cui parla.
const STEPS: { title: string; description: string; area: Area }[] = [
  {
    title: "Crea la tua azienda",
    description: "Registrati e importa il dossier dal Registro Imprese partendo dalla partita IVA.",
    area: "azienda",
  },
  {
    title: "Esplora i bandi",
    description: "Cerca e filtra nel catalogo, o lascia che «Bandi per te» faccia una prima selezione.",
    area: "bandi",
  },
  {
    title: "Lancia l'AI-check",
    description: "Leggi ammissibilità, punteggio e requisiti, punto per punto.",
    area: "aicheck",
  },
  {
    title: "Segui le scadenze",
    description: "Salva i bandi promettenti e tieni le loro scadenze sotto controllo nel calendario.",
    area: "scadenze",
  },
];

const STATS: { valore: string; etichetta: string; icon: LucideIcon; area: Area }[] = [
  {
    valore: LANDING_COPY.bandiValore,
    etichetta: LANDING_COPY.bandiEtichetta,
    icon: FileText,
    area: "bandi",
  },
  {
    valore: "4 livelli",
    etichetta: "Copertura: europea, nazionale, regionale e locale",
    icon: Globe,
    area: "home",
  },
  {
    valore: "0–100",
    etichetta: "Punteggio AI-check con citazioni",
    icon: Sparkles,
    area: "aicheck",
  },
  {
    valore: "Registro Imprese",
    etichetta: "Dati aziendali certificati",
    icon: Building2,
    area: "azienda",
  },
];

const REASONS: Voce[] = [
  {
    icon: ShieldCheck,
    title: "Verdetti verificabili",
    description:
      "L'AI-check non si limita a dare un voto: ogni requisito è motivato con la citazione esatta presa dal testo del bando. Niente scatole nere.",
    area: "aicheck",
  },
  {
    icon: Building2,
    title: "Dati certificati, non a memoria",
    description:
      "Il profilo della tua azienda nasce dai dati ufficiali del Registro Imprese, così l'analisi parte da informazioni affidabili.",
    area: "azienda",
  },
  {
    icon: Globe,
    title: "Copertura completa",
    description:
      "Bandi europei, nazionali, regionali e locali in un unico catalogo: una sola ricerca invece di decine di portali.",
    area: "bandi",
  },
  {
    icon: Layers,
    title: "Pensato per le imprese italiane",
    description:
      "Filtri, profili e report parlano la lingua delle PMI: ATECO, beneficiari, classi dimensionali, account per l'azienda.",
    area: "home",
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

/** Sulla fascia navy il primario («Inizia gratis») è `LinkButton variant="inverse"`
 *  (bianco pieno, testo navy, anello del focus bianco). Il secondario è un
 *  contorno bianco: `ghost` con le classi di `GHOST_SU_FASCIA` (testo bianco,
 *  fondo bianco/10 al passaggio, anello del focus bianco), più il bordo
 *  bianco/60 (≥ 3:1 sul fondo, il testo bianco ≥ 6,4:1 anche sul lato chiaro) e
 *  il padding pieno del pulsante `lg` (il `ghost` ne ha meno ai lati). */
const CONTORNO_SU_FASCIA = cn(GHOST_SU_FASCIA, "border-white/60 px-5 hover:border-white");

/** Due cerchi a filo bianco dietro i contenuti della fascia: solo decorazione,
 *  profondità senza un secondo gradiente. Il genitore è `relative overflow-hidden`. */
function CerchiFascia() {
  return (
    <>
      <span
        aria-hidden
        className="pointer-events-none absolute -top-40 -right-32 size-[32rem] rounded-full border border-white/10"
      />
      <span
        aria-hidden
        className="pointer-events-none absolute -bottom-56 left-1/3 size-[28rem] rounded-full border border-white/10"
      />
    </>
  );
}

/** Una voce in card: `IconChip` nel colore della sua area, titolo, testo. */
function VoceCard({ icon, title, description, area }: Voce) {
  return (
    <Card className="flex flex-col gap-3">
      <IconChip icon={icon} area={area} />
      <h3 className="font-sans text-row-title text-ink">{title}</h3>
      <p className="text-body text-ink-2">{description}</p>
    </Card>
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

  const aicheck = areaClassi("aicheck");

  return (
    <div className="min-h-dvh bg-sheet">
      {/* Testata chiara sopra la fascia dell'hero, dove stanno i pulsanti pieni. */}
      <header className="sticky top-0 z-40 border-b border-line bg-sheet">
        <Contenitore className="flex h-16 items-center justify-between gap-4">
          <Logo className={LOGO_TESTATA} />
          <nav className="hidden items-center gap-1 lg:flex" aria-label="Sezioni della pagina">
            {NAV_LINKS.map((link) => (
              <a
                key={link.href}
                href={link.href}
                className="rounded-control px-3 py-2 text-body font-medium text-ink-2 transition-colors duration-150 ease-uscita hover:bg-sunken hover:text-ink"
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
        {/* Hero sulla fascia navy (`bg-banda`): testo bianco, a destra le righe
            d'esempio come card bianche con ombra. */}
        <section
          aria-labelledby="hero-titolo"
          className="relative overflow-hidden bg-banda text-white"
        >
          <CerchiFascia />
          <Contenitore className="relative grid items-center gap-12 py-16 sm:py-20 lg:grid-cols-2 lg:gap-16 lg:py-24">
            <div className="flex flex-col items-start gap-6 motion-safe:animate-entrata">
              <SegnoTitolo className="w-12" />
              <h1 id="hero-titolo" className="text-title-hero text-white">
                Il radar sui bandi,
                <br />
                su misura per la tua impresa.
              </h1>
              <p className="max-w-lettura text-prose text-white/80">
                BandoFit raccoglie bandi europei, nazionali, regionali e locali e ti dice quali
                fanno per te — con filtri per impresa, schede chiare e un'analisi di compatibilità
                che cita il testo ufficiale.
              </p>
              <div className="flex flex-wrap gap-3">
                <LinkButton to="/registrati" size="lg" variant="inverse">
                  Inizia gratis
                </LinkButton>
                <LinkButton to="/login" size="lg" variant="ghost" className={CONTORNO_SU_FASCIA}>
                  Ho già un account
                </LinkButton>
              </div>
              <ul className="flex flex-col gap-2 text-body text-white/80">
                {[
                  LANDING_COPY.bandiClaim,
                  "Bandi UE, nazionali, regionali e locali",
                  "Dati dal Registro Imprese",
                ].map((punto) => (
                  <li key={punto} className="flex items-start gap-2">
                    {/* `fit` sul lato scuro della fascia, dove sta la lista: ≥ 3,5:1 (icone ≥ 3:1). */}
                    <Check className="mt-0.75 size-4 shrink-0 text-fit" aria-hidden />
                    {punto}
                  </li>
                ))}
              </ul>
            </div>
            <section aria-label="Esempio" className="flex flex-col gap-4">
              <p className="text-small text-white/80">
                Esempio: scadenza, valore e compatibilità di ogni bando, in una riga.
              </p>
              <RigheEsempio />
            </section>
          </Contenitore>
        </section>

        <Sezione
          fondo="desk"
          titolo="I bandi giusti esistono. Trovarli è il difficile."
          sottotitolo="Sono pubblicati su decine di portali diversi, con requisiti scritti in burocratese e scadenze facili da perdere. BandoFit li raccoglie, li rende leggibili e ti dice quali fanno per la tua azienda."
        >
          <div className="grid gap-6 sm:grid-cols-3">
            {PROBLEMI.map((voce) => (
              <VoceCard key={voce.title} {...voce} />
            ))}
          </div>
        </Sezione>

        <Sezione
          id="funzionalita"
          titolo="Tutto quello che serve per candidarti con criterio"
          sottotitolo="Dalla ricerca all'analisi di compatibilità: gli strumenti per passare dai «tanti bandi» ai «bandi giusti per te»."
        >
          {/* L'AI-check in evidenza: bordo sinistro e punti nel colore della sua area. */}
          <Panel
            aria-label="AI-check"
            className={cn("grid gap-6 p-6 sm:p-8 lg:grid-cols-2 lg:gap-12", aicheck.bordo)}
          >
            <div className="flex flex-col items-start gap-3">
              <IconChip icon={Sparkles} area="aicheck" size="lg" />
              <h3 className="text-title-section text-ink">AI-check</h3>
              <p className="text-body text-ink-2">
                Scopri se la tua azienda è ammissibile e quanto è compatibile con un punteggio da 0
                a 100. Ogni requisito è verificato e motivato con la citazione esatta presa dal
                bando: un verdetto che puoi controllare, non un voto calato dall'alto.
              </p>
            </div>
            <ul className="flex flex-col gap-3 self-center">
              {AI_CHECK_PUNTI.map((punto) => (
                <li
                  key={punto}
                  className={cn(
                    "flex items-center gap-3 rounded-control px-4 py-3 text-title-group",
                    aicheck.suSoft,
                  )}
                >
                  <Check className="size-4 shrink-0" aria-hidden />
                  {punto}
                </li>
              ))}
            </ul>
          </Panel>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((voce) => (
              <VoceCard key={voce.title} {...voce} />
            ))}
          </div>
        </Sezione>

        <Sezione
          id="come-funziona"
          fondo="desk"
          titolo="Dai dati al bando giusto, in quattro passi"
        >
          <ol className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((step, i) => (
              <li key={step.title} className="flex">
                <Card area={step.area} className="flex w-full flex-col gap-3">
                  <span
                    className={cn(
                      "flex size-11 items-center justify-center rounded-control text-figure-sm",
                      areaClassi(step.area).suSoft,
                    )}
                  >
                    {i + 1}
                  </span>
                  <h3 className="font-sans text-row-title text-ink">{step.title}</h3>
                  <p className="text-body text-ink-2">{step.description}</p>
                </Card>
              </li>
            ))}
          </ol>
        </Sezione>

        <Sezione titolo="Uno strumento serio, non l'ennesima lista di bandi">
          <div className="grid gap-6 sm:grid-cols-2">
            {REASONS.map((voce) => (
              <VoceCard key={voce.title} {...voce} />
            ))}
          </div>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {STATS.map((stat) => (
              <KpiCard
                key={stat.etichetta}
                etichetta={stat.etichetta}
                valore={stat.valore}
                icon={stat.icon}
                area={stat.area}
              />
            ))}
          </div>
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
                <Skeleton key={i} className="h-72 w-full rounded-panel" />
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
            // `pt-3`: lo spazio per l'etichetta «Consigliato» sul bordo in alto della card.
            <div className="grid gap-6 pt-3 sm:grid-cols-2 lg:grid-cols-4">
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
          <Card className="max-w-lettura px-5 py-1 sm:px-6">
            <Accordion
              className="border-y-0"
              items={FAQS.map((faq, i) => ({
                id: `faq-${i + 1}`,
                titolo: faq.q,
                children: <p>{faq.a}</p>,
              }))}
            />
          </Card>
        </Sezione>

        {/* Invito finale: la fascia navy come card, di seguito alle domande frequenti. */}
        <section aria-labelledby="invito-titolo" className="bg-sheet">
          <Contenitore className="pb-16 sm:pb-20">
            <div className="relative overflow-hidden rounded-panel bg-banda px-6 py-12 text-white shadow-card sm:px-10 sm:py-14 lg:px-14">
              <CerchiFascia />
              <div className="relative flex flex-col items-start gap-4">
                <SegnoTitolo className="w-12" />
                <h2 id="invito-titolo" className="text-title-page text-white">
                  Pronto a trovare i bandi giusti per la tua azienda?
                </h2>
                <p className="max-w-lettura text-prose text-white/80">
                  Crea il tuo account gratuito ed esplora subito il catalogo. Nessuna carta
                  richiesta.
                </p>
                <div className="mt-2 flex flex-wrap gap-3">
                  <LinkButton to="/registrati" size="lg" variant="inverse">
                    Inizia gratis
                  </LinkButton>
                  <LinkButton to="/login" size="lg" variant="ghost" className={CONTORNO_SU_FASCIA}>
                    Accedi
                  </LinkButton>
                </div>
              </div>
            </div>
          </Contenitore>
        </section>
      </main>

      <footer className="border-t border-line bg-sheet">
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
