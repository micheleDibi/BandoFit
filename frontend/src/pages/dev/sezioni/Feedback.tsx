import { Bookmark, SlidersHorizontal, Trash2 } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { Alert } from "../../../components/ui/Alert";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Drawer, type DrawerLato } from "../../../components/ui/Drawer";
import { IconButton } from "../../../components/ui/IconButton";
import { InlineError } from "../../../components/ui/InlineError";
import { Popover, usePopover } from "../../../components/ui/Popover";
import { ProgressBar } from "../../../components/ui/ProgressBar";
import { Spinner } from "../../../components/ui/Spinner";
import { TextLink } from "../../../components/ui/TextLink";
import { ToastProvider, useToast } from "../../../components/ui/Toast";
import { Tooltip } from "../../../components/ui/Tooltip";

/** Sezione della vetrina (`/app/_vetrina`, solo in sviluppo): ogni primitivo di
 *  feedback e sovrapposizione in tutti i suoi stati. Ha un `ToastProvider` suo,
 *  così funziona anche quando quello di `main.tsx` non c'è. */

export const titolo = "Feedback e sovrapposizioni";

export default function Feedback() {
  return (
    <ToastProvider>
      <div className="flex flex-col gap-12">
        <DemoAlert />
        <DemoInlineError />
        <DemoToast />
        <DemoConfirmDialog />
        <DemoSpinner />
        <DemoProgressBar />
        <DemoIconButton />
        <DemoTextLink />
        <DemoTooltip />
        <DemoPopover />
        <DemoDrawer />
      </div>
    </ToastProvider>
  );
}

function Blocco({ nome, nota, children }: { nome: string; nota: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-col gap-1 border-b border-line pb-3">
        <h3 className="text-title-group">{nome}</h3>
        <p className="text-small text-ink-3">{nota}</p>
      </div>
      {children}
    </section>
  );
}

function DemoAlert() {
  return (
    <Blocco nome="Alert" nota="Quattro toni, bordo intero, icona e parola. L'azione, se serve, a destra.">
      <div className="flex max-w-2xl flex-col gap-3">
        <Alert tono="info" azione={<TextLink to="/app/abbonamento">Attiva il rinnovo</TextLink>}>
          Il piano non si rinnova da solo: resta attivo fino al 31 lug 2027.
        </Alert>
        <Alert tono="ok">
          Invito inviato. L'azienda lo trova tra le sue candidature e può rispondere fino al 14 ott
          2026.
        </Alert>
        <Alert tono="attenzione" azione={<TextLink to="/app/abbonamento">Vedi i piani</TextLink>}>
          Hai raggiunto il numero massimo di aziende del tuo piano. Per gestirne altre rimuovine una
          o passa a un piano superiore.
        </Alert>
        <Alert
          tono="errore"
          azione={
            <Button variant="secondary" size="sm" onClick={() => undefined}>
              Riprova
            </Button>
          }
        >
          Impossibile aprire il pagamento. Riprova tra qualche istante.
        </Alert>
        <Alert tono="info" titolo="Con un titolo">
          La prima riga in grassetto, il resto sotto. Da usare quando l'avviso ha più di una frase.
        </Alert>
        <div role="status" aria-live="polite">
          <Alert tono="ok" ruolo="none">
            Con ruolo «none»: dentro una regione aria-live già montata (questa), che annuncia da
            sé. Senza, le due regioni annidate rischiano il doppio annuncio.
          </Alert>
        </div>
      </div>
    </Blocco>
  );
}

function DemoInlineError() {
  const campoId = useId();
  const erroreId = useId();
  return (
    <Blocco nome="InlineError" nota="Sotto il campo che lo ha causato, con il bordo del campo in danger.">
      <div className="flex max-w-sm flex-col gap-1.5">
        <label htmlFor={campoId} className="text-small font-medium text-ink">
          Partita IVA
        </label>
        <input
          id={campoId}
          defaultValue="0123456789"
          aria-invalid
          aria-describedby={erroreId}
          className="h-10 w-full rounded-control border border-danger bg-sheet px-3 text-body text-ink"
        />
        <InlineError id={erroreId}>La partita IVA deve essere composta da 11 cifre.</InlineError>
      </div>
    </Blocco>
  );
}

function DemoToast() {
  const { mostra } = useToast();
  return (
    <Blocco nome="Toast" nota="In basso a sinistra, sopra la pagina; sparisce da sola dopo 4 secondi. Al passaggio del mouse aspetta.">
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" onClick={() => mostra({ testo: "Preferenze salvate" })}>
          Mostra una conferma
        </Button>
        <Button
          variant="secondary"
          onClick={() => mostra({ testo: "Impossibile salvare le preferenze", tono: "errore" })}
        >
          Mostra un errore
        </Button>
        <Button
          variant="secondary"
          onClick={() => mostra({ testo: "Resta dieci secondi", durata: 10_000 })}
        >
          Mostra con durata lunga
        </Button>
      </div>
    </Blocco>
  );
}

function DemoConfirmDialog() {
  const [distruttiva, setDistruttiva] = useState(false);
  const [semplice, setSemplice] = useState(false);
  const [inCorso, setInCorso] = useState(false);
  const { mostra } = useToast();

  const conferma = () => {
    setInCorso(true);
    window.setTimeout(() => {
      setInCorso(false);
      setDistruttiva(false);
      mostra({ testo: "Azienda rimossa" });
    }, 1500);
  };

  return (
    <Blocco nome="ConfirmDialog" nota="La domanda nel titolo, le conseguenze nel testo, il pulsante che agisce a destra. Con «inCorso» la finestra non si chiude.">
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" onClick={() => setDistruttiva(true)}>
          Azione distruttiva
        </Button>
        <Button variant="secondary" onClick={() => setSemplice(true)}>
          Conferma semplice
        </Button>
      </div>
      <ConfirmDialog
        open={distruttiva}
        titolo="Rimuovere l'azienda dalle aziende gestite?"
        conferma="Rimuovi"
        distruttiva
        inCorso={inCorso}
        onConferma={conferma}
        onAnnulla={() => setDistruttiva(false)}
      >
        <p>
          <strong className="font-semibold text-ink">Fonderia Bertolotti S.r.l.</strong> esce dal
          selettore dell'azienda, dagli avvisi e dagli export. I suoi dati (bandi salvati,
          calendario, AI-check, dossier) restano conservati.
        </p>
      </ConfirmDialog>
      <ConfirmDialog
        open={semplice}
        titolo="Inviare la candidatura?"
        conferma="Invia"
        onConferma={() => {
          setSemplice(false);
          mostra({ testo: "Candidatura inviata" });
        }}
        onAnnulla={() => setSemplice(false)}
      >
        <p>L'azienda che ha aperto la call riceve subito il tuo profilo e può risponderti.</p>
      </ConfirmDialog>
    </Blocco>
  );
}

function DemoSpinner() {
  return (
    <Blocco nome="Spinner" nota="Tre taglie; con «label» è una regione status per le tecnologie assistive.">
      <div className="flex items-center gap-6">
        <Spinner size="sm" />
        <Spinner size="md" />
        <Spinner size="lg" />
        <Spinner label="Caricamento dei bandi" className="text-accent" />
        <span className="text-small text-ink-3">← con label, in accent</span>
      </div>
    </Blocco>
  );
}

function DemoProgressBar() {
  return (
    <Blocco nome="ProgressBar" nota="6px su sunken; accent per l'uso, fit per la compatibilità. Il numero sta sempre accanto, in parole.">
      <div className="flex max-w-sm flex-col gap-5">
        <div className="flex flex-col gap-1.5">
          <div className="flex justify-between text-small text-ink-2">
            <span>AI-check usati quest'anno</span>
            <span className="tabular-nums">3 su 10</span>
          </div>
          <ProgressBar valore={3} massimo={10} label="AI-check usati quest'anno" />
        </div>
        <div className="flex flex-col gap-1.5">
          <div className="flex justify-between text-small text-ink-2">
            <span>Requisiti soddisfatti</span>
            <span className="tabular-nums">4 su 4</span>
          </div>
          <ProgressBar valore={4} massimo={4} label="Requisiti soddisfatti" tono="fit" />
        </div>
        <div className="flex flex-col gap-1.5">
          <div className="flex justify-between text-small text-ink-2">
            <span>Vuota</span>
            <span className="tabular-nums">0 su 4</span>
          </div>
          <ProgressBar valore={0} massimo={4} label="Vuota" />
        </div>
      </div>
    </Blocco>
  );
}

function DemoIconButton() {
  return (
    <Blocco nome="IconButton" nota="32 o 40px, quiet o secondary, sempre con aria-label. La taglia dell'icona la decide il pulsante.">
      <div className="flex items-center gap-3">
        <IconButton label="Salva il bando" icon={<Bookmark />} size="sm" />
        <IconButton label="Salva il bando" icon={<Bookmark />} />
        <IconButton label="Filtri" icon={<SlidersHorizontal />} variant="secondary" size="sm" />
        <IconButton label="Filtri" icon={<SlidersHorizontal />} variant="secondary" />
        <IconButton label="Elimina" icon={<Trash2 />} disabled />
        <span className="text-small text-ink-3">← disabilitato</span>
      </div>
    </Blocco>
  );
}

function DemoTextLink() {
  return (
    <Blocco nome="TextLink" nota="Link in accent-hover, sottolineato al passaggio. Esterno: icona e nuova scheda. Niente «→».">
      <p className="max-w-xl text-body text-ink-2">
        Sfoglia i <TextLink to="/app/bandi">bandi</TextLink> oppure leggi il testo integrale sul{" "}
        <TextLink href="https://www.mise.gov.it" esterno>
          sito del ministero
        </TextLink>
        . Nell'elenco dei salvati puoi <TextLink to="/app/salvati">rivedere i bandi messi da parte</TextLink>.
      </p>
    </Blocco>
  );
}

function DemoTooltip() {
  return (
    <Blocco nome="Tooltip" nota="Su hover e focus, dopo 300 ms; Esc lo nasconde. Solo per spiegare un'icona, mai per informazioni necessarie.">
      <div className="flex items-center gap-3">
        <Tooltip testo="Salva il bando">
          <IconButton label="Salva il bando" icon={<Bookmark />} />
        </Tooltip>
        <Tooltip testo="Apri i filtri dell'elenco">
          <IconButton label="Filtri" icon={<SlidersHorizontal />} variant="secondary" />
        </Tooltip>
        <Tooltip testo="Anche su un pulsante con testo">
          <Button variant="secondary">Passa qui sopra</Button>
        </Tooltip>
      </div>
    </Blocco>
  );
}

function FiltroStato() {
  const { chiudi } = usePopover();
  const [scelti, setScelti] = useState<string[]>(["aperto"]);
  const opzioni = [
    { id: "aperto", label: "Aperto" },
    { id: "in-apertura", label: "In apertura" },
    { id: "chiuso", label: "Chiuso" },
  ];
  return (
    <div className="flex flex-col gap-3 p-2">
      <div className="flex flex-col gap-2">
        {opzioni.map((o) => (
          <label key={o.id} className="flex items-center gap-2.5 text-body">
            <input
              type="checkbox"
              checked={scelti.includes(o.id)}
              onChange={(e) =>
                setScelti((s) => (e.target.checked ? [...s, o.id] : s.filter((x) => x !== o.id)))
              }
              className="size-4 accent-accent"
            />
            {o.label}
          </label>
        ))}
      </div>
      <div className="flex justify-end gap-2 border-t border-line pt-3">
        <Button variant="ghost" size="sm" onClick={() => setScelti([])}>
          Azzera
        </Button>
        <Button size="sm" onClick={chiudi}>
          Applica
        </Button>
      </div>
    </div>
  );
}

function DemoPopover() {
  const [aperto, setAperto] = useState(false);
  return (
    <Blocco nome="Popover" nota="Pannello ancorato al trigger; Esc e clic fuori lo chiudono; Tab gira dentro. «Applica» usa chiudi() di usePopover(). Il secondo è allineato a destra e controllato.">
      <div className="flex flex-wrap items-center gap-3">
        <Popover
          label="Filtro per stato"
          trigger={
            <Button variant="secondary" size="sm">
              Stato
            </Button>
          }
        >
          <FiltroStato />
        </Popover>
        <Popover
          label="Altre azioni"
          align="end"
          open={aperto}
          onOpenChange={setAperto}
          trigger={<IconButton label="Altre azioni" icon={<SlidersHorizontal />} variant="secondary" />}
        >
          <div className="flex w-56 flex-col gap-2 p-2 text-body">
            <p className="text-ink-2">Aperto da fuori: {aperto ? "sì" : "no"}.</p>
            <Button variant="secondary" size="sm" onClick={() => setAperto(false)}>
              Chiudi dal genitore
            </Button>
          </div>
        </Popover>
        <Popover
          label="Senza elementi focalizzabili"
          trigger={
            <Button variant="secondary" size="sm">
              Solo testo
            </Button>
          }
        >
          <p className="max-w-64 p-2 text-small text-ink-2">
            Il focus va sul pannello stesso; Esc riporta al pulsante.
          </p>
        </Popover>
      </div>
    </Blocco>
  );
}

function DemoDrawer() {
  const [lato, setLato] = useState<DrawerLato | null>(null);
  const [conLogo, setConLogo] = useState(false);
  const voci = ["Home", "Bandi", "Bandi salvati", "Calendario", "AI-check"];
  return (
    <Blocco nome="Drawer" nota="Cassetto su <dialog> nativo, 320px, velo, focus intrappolato, Esc. Con «intestazione» il titolo resta solo per le tecnologie assistive.">
      <div className="flex flex-wrap gap-2">
        <Button
          variant="secondary"
          onClick={() => {
            setConLogo(false);
            setLato("sinistra");
          }}
        >
          Apri a sinistra
        </Button>
        <Button
          variant="secondary"
          onClick={() => {
            setConLogo(false);
            setLato("destra");
          }}
        >
          Apri a destra
        </Button>
        <Button
          variant="secondary"
          onClick={() => {
            setConLogo(true);
            setLato("sinistra");
          }}
        >
          Con intestazione
        </Button>
      </div>
      <Drawer
        open={lato !== null}
        onClose={() => setLato(null)}
        lato={lato ?? "sinistra"}
        titolo="Menu"
        intestazione={
          conLogo ? <span className="text-title-section text-ink">BandoFit</span> : undefined
        }
      >
        <nav aria-label="Esempio di menu" className="flex flex-col gap-0.5">
          {voci.map((v, i) => (
            <a
              key={v}
              href="#"
              onClick={(e) => e.preventDefault()}
              aria-current={i === 1 ? "page" : undefined}
              className="flex h-9 items-center rounded-control px-3 font-medium text-ink-2 hover:bg-sunken aria-[current=page]:bg-sheet aria-[current=page]:text-ink"
            >
              {v}
            </a>
          ))}
        </nav>
      </Drawer>
    </Blocco>
  );
}
