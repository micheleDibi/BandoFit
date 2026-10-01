import { Bookmark, CalendarPlus, Check, ExternalLink, Minus, Pencil, Sparkles, Users } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Accordion } from "../../../components/ui/Accordion";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Due, tempoRelativo } from "../../../components/ui/Due";
import { DefinitionList, Facts } from "../../../components/ui/Facts";
import { Fit } from "../../../components/ui/Fit";
import { Page, type VariantePage } from "../../../components/ui/Page";
import { PageHeader } from "../../../components/ui/PageHeader";
import { Panel } from "../../../components/ui/Panel";
import { Section, SectionHeader } from "../../../components/ui/SectionHeader";
import { Status, type TonoStatus } from "../../../components/ui/Status";
import { Stepper } from "../../../components/ui/Stepper";
import { Table, Td, Th } from "../../../components/ui/Table";
import { TabPanel, Tabs } from "../../../components/ui/Tabs";
import { formatDate, formatEur } from "../../../lib/format";
import { prezzoDisplay } from "../../../lib/prezzo";

export const titolo = "Struttura e dati";

// «Oggi» fisso: i dati d'esempio vengono dalle tavole (5 ott 2026 → «tra 5 giorni»).
const OGGI = new Date(2026, 8, 30);

function Esempio({ nome, children }: { nome: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <p className="text-small text-ink-3">{nome}</p>
      {children}
    </div>
  );
}

const STATI: { tono: TonoStatus; parola: string }[] = [
  { tono: "aperto", parola: "Aperto" },
  { tono: "in-apertura", parola: "In apertura" },
  { tono: "in-scadenza", parola: "In scadenza" },
  { tono: "attenzione", parola: "Da rifare" },
  { tono: "errore", parola: "Pagamento non riuscito" },
  { tono: "chiuso", parola: "Chiuso" },
  { tono: "neutro", parola: "Spento" },
];

const SCADENZE: { nome: string; data: string | null; ora?: string }[] = [
  { nome: "Urgente (≤ 7 giorni)", data: "2026-10-05" },
  { nome: "Vicina (≤ 30 giorni)", data: "2026-10-09" },
  { nome: "Lontana", data: "2026-10-31", ora: "12:00:00" },
  { nome: "Lontana", data: "2026-11-30" },
  { nome: "Oggi", data: "2026-09-30" },
  { nome: "Domani", data: "2026-10-01" },
  { nome: "Passata (ieri)", data: "2026-09-29" },
  { nome: "Passata", data: "2026-07-31" },
  { nome: "Senza data", data: null },
];

const PIANI = [
  { nome: "Gratuito", per: "Per iniziare a esplorare i bandi", ai: "3", avvisi: "Non inclusi", account: "1", prezzo: prezzoDisplay("gratis", null, null), attuale: false },
  { nome: "Smart", per: "Per professionisti che seguono pochi bandi mirati", ai: "5", avvisi: "Dopo 14 giorni dalla pubblicazione", account: "1", prezzo: prezzoDisplay("importo", null, 59), attuale: false },
  { nome: "Pro", per: "Per aziende e consulenti con esigenze continuative", ai: "20", avvisi: "Dopo 7 giorni dalla pubblicazione", account: "Fino a 3", prezzo: prezzoDisplay("importo", null, 149), attuale: false },
  { nome: "Advisor", per: "Per studi e advisor che gestiscono più clienti", ai: "100", avvisi: "Il giorno stesso della pubblicazione", account: "Fino a 10", prezzo: prezzoDisplay("importo", null, 299), attuale: true },
  { nome: "Su misura", per: "Per chi ha esigenze particolari", ai: "Si definiscono insieme a te", avvisi: "", account: "", prezzo: prezzoDisplay("su_richiesta", null, null), attuale: false },
];

const SCHEDE_ABBONAMENTO = [
  { id: "piano", label: "Piano" },
  { id: "addon", label: "Add-on" },
  { id: "pagamento", label: "Pagamento e fatturazione" },
  { id: "acquisti", label: "Acquisti" },
];

const SCHEDE_PARTENARIATI = [
  { id: "per-te", label: "Per te", count: 3 },
  { id: "tutte", label: "Tutte le call", count: 28 },
  { id: "salvate", label: "Call salvate", count: 2 },
  { id: "mie", label: "Le tue call", count: 1 },
  { id: "candidature", label: "Candidature e inviti", count: 3 },
  { id: "conversazioni", label: "Conversazioni", count: 0 },
];

const PASSI_WIZARD = ["Bando", "Regole del bando", "Requisiti", "Posizioni", "Testi", "Anteprima", "Pubblicazione"];

const VARIANTI_PAGE: { variante: VariantePage; nome: string }[] = [
  { variante: "elenco", nome: "elenco (1280px, centrato)" },
  { variante: "sezioni", nome: "sezioni (1040px, centrato)" },
  { variante: "flusso", nome: "flusso (760px, centrato)" },
];

function Riquadro({ children }: { children: ReactNode }) {
  return <div className="rounded-control bg-sunken px-4 py-3 text-small text-ink-2">{children}</div>;
}

export default function Struttura() {
  const [schedaAbbonamento, setSchedaAbbonamento] = useState("piano");
  const [schedaPartenariati, setSchedaPartenariati] = useState("per-te");
  const [passo, setPasso] = useState(3);
  const [passoBozza, setPassoBozza] = useState(1);

  return (
    <div className="flex flex-col gap-12">
      <Section>
        <SectionHeader titolo="Status" />
        <p className="text-small text-ink-3">
          Pillola nel colore del tono: aperto fit, in apertura accent, in scadenza warm, attenzione
          warning, errore danger, chiuso e neutro neutral. La forma del pallino aiuta a non contare
          solo sul colore.
        </p>
        <div className="flex flex-wrap gap-3">
          {STATI.map((s) => (
            <Status key={s.tono} tono={s.tono}>
              {s.parola}
            </Status>
          ))}
        </div>
      </Section>

      <Section>
        <SectionHeader titolo="Due" azione={<span className="text-small text-ink-3">oggi = 30 set 2026</span>} />
        <p className="text-small text-ink-3">
          Tessera per urgenza: entro 7 giorni warm, entro 30 warning, oltre accent; passata o con
          conConto falso, neutra.
        </p>
        <div className="flex flex-wrap gap-x-8 gap-y-6">
          {SCADENZE.map((s, i) => (
            <Esempio key={i} nome={s.nome}>
              <Due data={s.data} ora={s.ora} oggi={OGGI} />
            </Esempio>
          ))}
          <Esempio nome="conConto={false} (bando chiuso)">
            <Due data="2026-10-05" oggi={OGGI} conConto={false} />
          </Esempio>
        </div>
      </Section>

      <Section>
        <SectionHeader titolo="Fit" />
        <div className="flex flex-wrap gap-8">
          {[0, 1, 2, 3, 4].map((n) => (
            <Fit key={n} soddisfatti={n} totale={4} />
          ))}
          <Fit soddisfatti={3} totale={5} label="Requisiti soddisfatti: 3 su 5" />
        </div>
        <Esempio nome='variante="anello"'>
          <div className="flex flex-wrap gap-8">
            {[0, 2, 4].map((n) => (
              <Fit key={n} soddisfatti={n} totale={4} variante="anello" />
            ))}
          </div>
        </Esempio>
      </Section>

      <Section>
        <SectionHeader titolo="Panel" />
        <div className="grid gap-6 lg:grid-cols-3">
          <Panel titolo="Fa per te?" icon={Sparkles} area="aicheck" azione={<Fit soddisfatti={2} totale={4} />}>
            <p className="text-small text-ink-2">
              Requisiti del bando soddisfatti da Officine Rinaldi S.r.l., tutte le sedi comprese.
            </p>
            <ul className="divide-y divide-line">
              {[
                { ok: true, nome: "Regione", nota: "Piemonte: hai una sede a Torino" },
                { ok: true, nome: "Settore", nota: "Ristrutturazione, recupero, riqualificazione" },
                { ok: false, nome: "Codice ATECO", nota: "Il bando chiede 94, il tuo è 25.62" },
                { ok: false, nome: "Beneficiari", nota: "Il bando è per enti del Terzo Settore" },
              ].map((r) => (
                <li key={r.nome} className="flex gap-2 py-2.5">
                  {r.ok ? (
                    <Check className="mt-0.5 size-4 shrink-0 text-fit-ink" strokeWidth={1.75} role="img" aria-label="Soddisfatto" />
                  ) : (
                    <Minus className="mt-0.5 size-4 shrink-0 text-ink-3" strokeWidth={1.75} role="img" aria-label="Non soddisfatto" />
                  )}
                  <div className="flex flex-col">
                    <span className="text-small font-semibold text-ink">{r.nome}</span>
                    <span className="text-small text-ink-2">{r.nota}</span>
                  </div>
                </li>
              ))}
            </ul>
            <Button variant="secondary">Avvia AI-check</Button>
            <p className="text-small text-ink-3">
              L'AI confronta ogni requisito con i dati della tua azienda e cita i passaggi del bando.
              Ti restano 96 AI-check su 100 quest'anno.
            </p>
          </Panel>
          <Panel titolo="Partenariato" icon={Users} area="partenariati">
            <p className="text-small text-ink-2">
              Scopri se questo bando ammette o richiede partner: leggiamo per te i documenti ufficiali.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="secondary" size="sm">Vedi le regole</Button>
              <Button variant="ghost" size="sm">Crea una call</Button>
            </div>
          </Panel>
          <Panel>
            <p className="text-small text-ink-2">Pannello senza titolo, solo contenuto.</p>
          </Panel>
        </div>
      </Section>

      <Section>
        <SectionHeader titolo="SectionHeader e Section" />
        <Section>
          <SectionHeader
            titolo="Regole di partenariato"
            azione={<Button variant="ghost" size="sm">Mostra le regole</Button>}
            id="esempio-regole"
          />
          <p className="text-body text-ink-2">
            Chi può partecipare insieme a chi: forme ammesse, numero di partner, quote e documenti.
          </p>
        </Section>
        <Section>
          <SectionHeader titolo="Titolo di terzo livello" livello={3} />
          <p className="text-body text-ink-2">Sezione con `livello={3}`.</p>
        </Section>
      </Section>

      <Section>
        <SectionHeader titolo="Facts" />
        <Facts
          items={[
            { etichetta: "Scadenza", valore: `${formatDate("2026-10-31")}, ore 12:00`, nota: tempoRelativo("2026-10-31", OGGI) },
            { etichetta: "Dotazione", valore: formatEur(200000) },
            { etichetta: "Apertura", valore: formatDate("2026-09-25") },
          ]}
          azione={
            <Button variant="ghost" size="sm">
              <CalendarPlus className="size-4" strokeWidth={1.75} aria-hidden />
              Aggiungi la scadenza al calendario
            </Button>
          }
        />
      </Section>

      <Section>
        <SectionHeader titolo="DefinitionList" />
        <DefinitionList
          items={[
            { etichetta: "Ragione sociale", valore: "Officine Rinaldi S.r.l." },
            { etichetta: "Forma giuridica", valore: "Società a responsabilità limitata" },
            { etichetta: "Partita IVA", valore: "01234567890" },
            { etichetta: "Codice ATECO", valore: "25.62", nota: "Lavori di meccanica generale" },
            { etichetta: "Settore", valore: "Industria e manifattura" },
            { etichetta: "Regione", valore: "Piemonte" },
            { etichetta: "Dimensione", valore: "Piccola impresa", nota: "meno di 50 dipendenti" },
            { etichetta: "Sede legale", valore: "Via Pietro Cossa 114, 10146 Torino (TO)" },
          ]}
        />
      </Section>

      <Section>
        <SectionHeader titolo="Table" />
        <Table>
          <thead>
            <tr>
              <Th>Piano</Th>
              <Th>AI-check all'anno</Th>
              <Th>Avvisi email sui nuovi bandi</Th>
              <Th>Account aziendali</Th>
              <Th numerica>Prezzo</Th>
              <Th>
                <span className="sr-only">Azione</span>
              </Th>
            </tr>
          </thead>
          <tbody>
            {PIANI.map((p) => (
              <tr key={p.nome}>
                <Td>
                  <div className="flex flex-col gap-0.5">
                    <span className="font-semibold text-ink">{p.nome}</span>
                    <span className="text-small text-ink-2">{p.per}</span>
                  </div>
                </Td>
                <Td>{p.ai}</Td>
                <Td>{p.avvisi}</Td>
                <Td>{p.account}</Td>
                <Td numerica>
                  <span className="font-semibold text-ink">{p.prezzo.testo}</span>
                  {p.prezzo.conSuffissoPeriodo && <span className="text-small text-ink-2"> /anno</span>}
                </Td>
                <Td className="text-right">
                  {p.attuale ? (
                    <Status tono="neutro">Piano attuale</Status>
                  ) : (
                    <Button variant="secondary" size="sm">
                      {p.prezzo.suRichiesta ? "Richiedi una consulenza" : `Passa a ${p.nome}`}
                    </Button>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Section>

      <Section>
        <SectionHeader titolo="Tabs e TabPanel" />
        <Esempio nome="Con TabPanel collegati (stesso prefisso); stato locale: nelle pagine lo dà useTab">
          <Tabs
            tabs={SCHEDE_ABBONAMENTO}
            attivo={schedaAbbonamento}
            onChange={setSchedaAbbonamento}
            ariaLabel="Sezioni dell'abbonamento"
            prefisso="abbonamento"
          />
          {SCHEDE_ABBONAMENTO.map((s) => (
            <TabPanel key={s.id} id={s.id} attivo={schedaAbbonamento} prefisso="abbonamento">
              <p className="text-body text-ink-2">Contenuto della scheda «{s.label}».</p>
            </TabPanel>
          ))}
        </Esempio>
        <Esempio nome="Con contatori, senza pannelli (id da useId, nessun aria-controls)">
          <Tabs
            tabs={SCHEDE_PARTENARIATI}
            attivo={schedaPartenariati}
            onChange={setSchedaPartenariati}
            ariaLabel="Sezioni dei partenariati"
          />
        </Esempio>
      </Section>

      <Section>
        <SectionHeader titolo="Accordion" />
        <Accordion
          items={[
            { id: "esempio-anagrafica", titolo: "Anagrafica", aperto: true, children: <p>Ragione sociale, forma giuridica, partita IVA e codice fiscale.</p> },
            { id: "esempio-sede", titolo: "Sede e contatti", aperto: true, children: <p>Sede legale, unità locali, PEC e telefono.</p> },
            { id: "esempio-attivita", titolo: "Attività", children: <p>Codice ATECO, settore, dimensione e dipendenti.</p> },
            { id: "esempio-soci", titolo: "Soci e cariche", children: <p>Compagine sociale e amministratori.</p> },
          ]}
        />
      </Section>

      <Section>
        <SectionHeader titolo="Stepper" />
        <Esempio nome={`Con onVai (passi fatti cliccabili): passo ${passo + 1} di ${PASSI_WIZARD.length}`}>
          <Stepper passi={PASSI_WIZARD} corrente={passo} onVai={setPasso} />
        </Esempio>
        <Esempio
          nome={`Con raggiunto (bozza ripresa): passo ${passoBozza + 1} di ${PASSI_WIZARD.length}, cliccabili fino al 5`}
        >
          <Stepper passi={PASSI_WIZARD} corrente={passoBozza} raggiunto={4} onVai={setPassoBozza} />
        </Esempio>
        <Esempio nome="Senza onVai, al primo passo">
          <Stepper passi={["Bando", "Regole del bando", "Requisiti"]} corrente={0} />
        </Esempio>
      </Section>

      <Section>
        <SectionHeader titolo="PageHeader" />
        <Esempio nome="Pagina a sezioni: titolo, descrizione, azioni">
          <PageHeader
            titolo="Dati azienda"
            descrizione="I dati di Officine Rinaldi S.r.l., alla base della compatibilità e dell'AI-check."
            azioni={
              <>
                <Button variant="ghost">Importa da partita IVA</Button>
                <Button variant="secondary">
                  <Pencil className="size-4" strokeWidth={1.75} aria-hidden />
                  Modifica
                </Button>
              </>
            }
          />
        </Esempio>
        <Esempio nome="Con area: la fascia navy, IconChip dell'area, azioni inverse">
          <PageHeader
            area="azienda"
            titolo="Dati azienda"
            descrizione="I dati di Officine Rinaldi S.r.l., alla base della compatibilità e dell'AI-check."
            azioni={
              <Button variant="inverse">
                <Pencil className="size-4" strokeWidth={1.75} aria-hidden />
                Modifica
              </Button>
            }
          />
        </Esempio>
        <Esempio nome="Con area, ritorno e riga sopra (scheda del bando)">
          <PageHeader
            area="bandi"
            stileTitolo="bando"
            indietro={{ label: "Bandi", to: "/app/bandi" }}
            sopra={
              <>
                <Status tono="aperto">Aperto</Status>
                <Badge>Bando regionale</Badge>
                <Badge>Fondo perduto</Badge>
              </>
            }
            titolo="Contributi Piemonte 2026 per Società di Mutuo Soccorso storiche"
            descrizione="Regione Piemonte"
            azioni={
              <Button variant="inverse">
                Vai al bando
                <ExternalLink className="size-4" strokeWidth={1.75} aria-hidden />
              </Button>
            }
          />
        </Esempio>
        <Esempio nome="Senza area (come prima). Scheda del bando: ritorno, riga sopra, titolo grande, azioni con nota">
          <PageHeader
            stileTitolo="bando"
            indietro={{ label: "Bandi", to: "/app/bandi" }}
            sopra={
              <>
                <Status tono="aperto">Aperto</Status>
                <Badge>Bando regionale</Badge>
                <Badge>Fondo perduto</Badge>
              </>
            }
            titolo="Contributi Piemonte 2026 per Società di Mutuo Soccorso storiche"
            descrizione="Regione Piemonte"
            azioni={
              <div className="flex flex-col items-end gap-2">
                <div className="flex gap-2">
                  <Button variant="secondary">
                    <Bookmark className="size-4" strokeWidth={1.75} aria-hidden />
                    Salva il bando
                  </Button>
                  <Button>
                    Vai al bando
                    <ExternalLink className="size-4" strokeWidth={1.75} aria-hidden />
                  </Button>
                </div>
                <span className="text-small text-ink-2">Sito ufficiale: bandi.regione.piemonte.it</span>
              </div>
            }
          />
        </Esempio>
      </Section>

      <Section>
        <SectionHeader titolo="Page" />
        {VARIANTI_PAGE.map((v) => (
          <Esempio key={v.variante} nome={`variante="${v.variante}" — ${v.nome}`}>
            <div className="border border-dashed border-line-control">
              <Page variante={v.variante}>
                <Riquadro>Contenuto della pagina</Riquadro>
              </Page>
            </div>
          </Esempio>
        ))}
        <Esempio nome='variante="dettaglio" — 1280px centrato, intestazione a tutta larghezza, laterale 340px'>
          <div className="border border-dashed border-line-control">
            <Page
              variante="dettaglio"
              intestazione={<Riquadro>intestazione (PageHeader, Facts)</Riquadro>}
              laterale={
                <>
                  <Panel titolo="Pannello 1">
                    <p className="text-small text-ink-2">Nella colonna laterale.</p>
                  </Panel>
                  <Panel titolo="Pannello 2">
                    <p className="text-small text-ink-2">Sotto il contenuto su mobile.</p>
                  </Panel>
                </>
              }
            >
              <Riquadro>Contenuto principale</Riquadro>
            </Page>
          </div>
        </Esempio>
      </Section>
    </div>
  );
}
