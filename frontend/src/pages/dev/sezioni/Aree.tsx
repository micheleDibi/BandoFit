import { Bookmark, CalendarDays, FileText, Sparkles, Users } from "lucide-react";
import type { ReactNode } from "react";
import { BarChart } from "../../../components/ui/BarChart";
import { Button } from "../../../components/ui/Button";
import { IconChip } from "../../../components/ui/IconChip";
import { KpiCard } from "../../../components/ui/KpiCard";
import { PageHeader } from "../../../components/ui/PageHeader";
import { ProgressBar } from "../../../components/ui/ProgressBar";
import { ProgressRing } from "../../../components/ui/ProgressRing";
import { AREE, areaClassi, areaIcona, type Area } from "../../../components/ui/area";
import { cn } from "../../../lib/cn";

/** Sezione della vetrina (solo in sviluppo): la veste «Navy deciso». Colori
 *  d'area, la fascia, gli indicatori e i grafici, con dati finti e senza rete. */
export const titolo = "Aree, fascia e grafici";

function Blocco({ titolo, nota, children }: { titolo: string; nota?: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-4">
      <div className="border-b border-line pb-3">
        <h3 className="text-title-section text-ink">{titolo}</h3>
        {nota && <p className="text-small text-ink-3">{nota}</p>}
      </div>
      {children}
    </section>
  );
}

const NOMI: Record<Area, string> = {
  home: "Home",
  bandi: "Bandi",
  aicheck: "AI-check",
  scadenze: "Calendario",
  partenariati: "Partenariati",
  consulenze: "Consulenze",
  azienda: "Dati azienda",
  account: "Account",
  admin: "Admin",
};

const SCADENZE_PER_MESE = [
  { etichetta: "ott", valore: 12 },
  { etichetta: "nov", valore: 8 },
  { etichetta: "dic", valore: 15 },
  { etichetta: "gen", valore: 4 },
  { etichetta: "feb", valore: 9 },
  { etichetta: "mar", valore: 0 },
];

const CALL_PER_REGIONE = [
  { etichetta: "Piemonte", valore: 7 },
  { etichetta: "Lombardia", valore: 11 },
  { etichetta: "Veneto", valore: 5 },
  { etichetta: "Lazio", valore: 3 },
];

export default function Aree() {
  return (
    <div className="flex flex-col gap-12">
      <Blocco
        titolo="Colori d'area e IconChip"
        nota="Ogni area ha base, soft e ink (classi statiche da ui/area.ts). IconChip: fondo soft, icona ink, tre taglie; inverse sulla fascia."
      >
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {AREE.map((area) => {
            const classi = areaClassi(area);
            return (
              <li
                key={area}
                className="flex items-center gap-3 rounded-panel border border-line bg-sheet p-3 shadow-card"
              >
                <IconChip icon={areaIcona[area]} area={area} size="sm" />
                <IconChip icon={areaIcona[area]} area={area} size="md" />
                <IconChip icon={areaIcona[area]} area={area} size="lg" />
                <div className="flex min-w-0 flex-col gap-1">
                  <span className={cn("text-title-group", classi.ink)}>{NOMI[area]}</span>
                  <span className="flex items-center gap-1" aria-hidden>
                    <span className={cn("size-3 rounded-xs", classi.base)} />
                    <span className={cn("size-3 rounded-xs ring-1 ring-line ring-inset", classi.soft)} />
                    <code className="text-caption text-ink-3">{area}</code>
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
        <div className="flex flex-wrap items-center gap-3 rounded-panel bg-banda p-4">
          {AREE.map((area) => (
            <IconChip key={area} icon={areaIcona[area]} area={area} inverse />
          ))}
          <span className="text-small text-white/80">inverse: bianco al 15% sulla fascia</span>
        </div>
      </Blocco>

      <Blocco
        titolo="PageHeader con area"
        nota="La fascia (bg-banda, l'unico gradiente): IconChip inverse, testi bianchi, ritorno e riga sopra al bianco/80, azioni inverse. Home usa il navy senza colore d'area."
      >
        <PageHeader
          area="home"
          titolo="Buongiorno, Giulia"
          descrizione="Officine Rinaldi S.r.l.: 3 bandi adatti, 2 scadenze questa settimana."
          azioni={<Button variant="inverse">Cerca nei bandi</Button>}
        />
        <PageHeader
          area="partenariati"
          indietro={{ label: "Partenariati", to: "/app/partenariati" }}
          titolo="Call di partenariato"
          descrizione="Cerca partner per i bandi che richiedono un raggruppamento."
          azioni={<Button variant="inverse">Crea una call</Button>}
        />
      </Blocco>

      <Blocco
        titolo="KpiCard"
        nota="Numero grande contato da 0 al valore (~600 ms, fermo con il movimento ridotto; lo screen reader legge solo il finale), IconChip d'area, link opzionale sull'intera card."
      >
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <KpiCard
            etichetta="Bandi adatti"
            valore={128}
            nota="12 nuovi questa settimana"
            icon={FileText}
            area="bandi"
            to="/app/bandi"
          />
          <KpiCard etichetta="Scadenze entro 30 giorni" valore={7} icon={CalendarDays} area="scadenze">
            <ProgressBar valore={2} massimo={7} label="Scadenze entro 7 giorni" />
            <p className="mt-1.5 text-caption text-ink-3">2 entro 7 giorni</p>
          </KpiCard>
          <KpiCard etichetta="AI-check disponibili" valore={96} nota="su 100 quest'anno" icon={Sparkles} area="aicheck">
            <ProgressBar valore={4} massimo={100} label="AI-check usati: 4 su 100" />
          </KpiCard>
          <KpiCard etichetta="Dotazione dei bandi salvati" valore="1.250.000 €" icon={Bookmark} area="bandi" />
        </div>
      </Blocco>

      <Blocco
        titolo="ProgressRing"
        nota="Anello SVG: pista sunken, arco nel tono (fit, accent, warm o un'area), valore al centro o children. role=img con label in parole."
      >
        <div className="flex flex-wrap items-center gap-8">
          <ProgressRing value={3} max={4} tono="fit" label="Compatibilità: 3 requisiti su 4">
            3/4
          </ProgressRing>
          <ProgressRing value={42} max={100} tono="accent" label="Profilo completo al 42 per cento">
            42%
          </ProgressRing>
          <ProgressRing value={5} max={7} tono="warm" label="5 scadenze su 7 entro la settimana" />
          <ProgressRing value={96} max={100} tono="aicheck" size={96} label="AI-check disponibili: 96 su 100" />
          <ProgressRing value={2} max={5} tono="partenariati" size={40} label="Partner trovati: 2 su 5" />
          <ProgressRing value={0} max={4} tono="fit" label="Compatibilità: 0 requisiti su 4" />
        </div>
      </Blocco>

      <Blocco
        titolo="BarChart"
        nota="Barre verticali in SVG, valore sopra e etichetta sotto (il numero sta sempre scritto), largo quanto il contenitore; le barre crescono all'arrivo."
      >
        <div className="grid gap-6 lg:grid-cols-2">
          <div className="flex flex-col gap-3 rounded-panel border border-line bg-sheet p-5 shadow-card">
            <p className="text-title-group text-ink">Scadenze dei bandi salvati per mese</p>
            <BarChart
              dati={SCADENZE_PER_MESE}
              tono="scadenze"
              ariaLabel="Scadenze dei bandi salvati per mese: ottobre 12, novembre 8, dicembre 15, gennaio 4, febbraio 9, marzo 0."
            />
          </div>
          <div className="flex flex-col gap-3 rounded-panel border border-line bg-sheet p-5 shadow-card">
            <p className="flex items-center gap-2 text-title-group text-ink">
              <Users className="size-4 text-area-partenariati-ink" aria-hidden />
              Call aperte per regione
            </p>
            <BarChart
              dati={CALL_PER_REGIONE}
              tono="partenariati"
              altezza={160}
              ariaLabel="Call aperte per regione: Piemonte 7, Lombardia 11, Veneto 5, Lazio 3."
            />
          </div>
        </div>
      </Blocco>
    </div>
  );
}
