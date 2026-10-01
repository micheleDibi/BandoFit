import { Pencil, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Badge } from "../../../components/ui/Badge";
import { Button, LinkButton } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Combobox } from "../../../components/ui/Combobox";
import { Dialog } from "../../../components/ui/Dialog";
import { SelectField, TextareaField, TextField } from "../../../components/ui/Field";
import { Menu, MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Pagination } from "../../../components/ui/Pagination";
import { TagSelect } from "../../../components/ui/TagSelect";
import { EmptyState, ErrorState, Skeleton } from "../../../components/ui/states";

/** Sezione della vetrina (solo in sviluppo): i primitivi esistenti rivisti con
 *  i token, ognuno nei suoi stati. Le props sono quelle di sempre. */
export const titolo = "Primitivi";

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

/** La scala chiusa degli stili di testo (docs/design-system.md), dal più grande. */
const STILI_DI_TESTO = [
  { classe: "text-title-hero", nota: "Sora 600, 32/40 e 40/48 da sm: solo l'h1 della landing" },
  { classe: "text-title-bando", nota: "Sora 600, 28/36: il titolo del bando e la promessa dell'accesso" },
  { classe: "text-title-page", nota: "Sora 600, 24/32: titolo di pagina" },
  { classe: "text-title-section", nota: "Sora 600, 18/26: titolo di sezione" },
  { classe: "text-figure", nota: "Sora 600, 28/32: cifra in evidenza" },
  { classe: "text-figure-sm", nota: "Sora 600, 18/24: valore nei fatti, importo nelle righe" },
  { classe: "text-due-day", nota: "Sora 600, 26/28: il giorno della scadenza" },
  { classe: "text-prose", nota: "Inter 400, 16/26: testo lungo" },
  { classe: "text-row-title", nota: "Inter 600, 16/24: titolo di una riga" },
  { classe: "text-body", nota: "Inter 400, 14/22: la taglia di base" },
  { classe: "text-title-group", nota: "Inter 600, 14/20: titolo di gruppo, pulsanti" },
  { classe: "text-small", nota: "Inter 400, 13/20: metadati, etichette, aiuto" },
  { classe: "text-caption", nota: "Inter 500, 12/16: la taglia minima" },
] as const;

const REGIONI = [
  { id: 1, label: "Piemonte" },
  { id: 2, label: "Lombardia" },
  { id: 3, label: "Veneto", sublabel: "Nord-est" },
  { id: 4, label: "Emilia-Romagna" },
  { id: 5, label: "Toscana" },
];

export default function Primitivi() {
  const [dialogAperto, setDialogAperto] = useState(false);
  const [dialogBloccato, setDialogBloccato] = useState(false);
  const [pagina, setPagina] = useState(3);
  const [regione, setRegione] = useState<number | null>(2);
  const [settori, setSettori] = useState<number[]>([1]);

  return (
    <div className="flex flex-col gap-12">
      <Blocco titolo="Stili di testo" nota="La scala chiusa: niente taglie arbitrarie.">
        <dl className="flex flex-col divide-y divide-line">
          {STILI_DI_TESTO.map((stile) => (
            <div key={stile.classe} className="flex flex-col gap-1 py-3">
              <dt className={`${stile.classe} text-ink`}>Contributi per la digitalizzazione</dt>
              <dd className="text-small text-ink-3">
                <code>{stile.classe}</code>: {stile.nota}
              </dd>
            </div>
          ))}
        </dl>
      </Blocco>

      <Blocco titolo="Button" nota="Un solo pulsante pieno per schermata; ghost è il testuale.">
        <div className="flex flex-wrap items-center gap-2">
          <Button>Salva le modifiche</Button>
          <Button variant="secondary">Annulla</Button>
          <Button variant="ghost">Vedi tutti</Button>
          <Button variant="danger">Rimuovi</Button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="md">Medio</Button>
          <Button variant="secondary" size="sm">
            Piccolo
          </Button>
          <Button variant="secondary" size="lg">
            Grande
          </Button>
          <Button variant="ghost" size="sm">
            Piccolo
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button loading>Salvataggio in corso</Button>
          <Button variant="secondary" disabled>
            Non disponibile
          </Button>
          <Button variant="secondary" disabled>
            Non disponibile
          </Button>
          <LinkButton to="/app/bandi" variant="secondary">
            Cerca nei bandi
          </LinkButton>
        </div>
      </Blocco>

      <Blocco titolo="Card" nota="Bordo line, raggio panel, senza ombra, padding 20 (p-* passato vince).">
        <div className="grid gap-4 sm:grid-cols-2">
          <Card>
            <p className="text-title-group text-ink">Piano Base</p>
            <p className="mt-1 text-body text-ink-2">Padding di default.</p>
          </Card>
          <Card className="p-3">
            <p className="text-title-group text-ink">Piano Pro</p>
            <p className="mt-1 text-body text-ink-2">Padding ridotto con p-3.</p>
          </Card>
        </div>
      </Blocco>

      <Blocco titolo="Badge" nota="Etichetta neutra per ogni tono: lo stato in parole lo fa Status.">
        <div className="flex flex-wrap items-center gap-2">
          <Badge>Fondo perduto</Badge>
          <Badge tone="brand">Regione Piemonte</Badge>
          <Badge tone="emerald">Voucher</Badge>
          <Badge tone="amber">Credito d'imposta</Badge>
          <Badge tone="red">Micro impresa</Badge>
        </div>
        <div className="flex w-48 flex-col items-start gap-1">
          <p className="text-small text-ink-3">Testo lungo in 192px: va a capo, minimo 24px.</p>
          <Badge>Micro, piccole e medie imprese del settore agricolo</Badge>
        </div>
      </Blocco>

      <Blocco titolo="Field" nota="TextField, TextareaField, SelectField: normale, con aiuto, con errore, disabilitato.">
        <div className="grid max-w-3xl gap-4 sm:grid-cols-2">
          <TextField label="Ragione sociale" placeholder="Officine Rinaldi S.r.l." required />
          <TextField
            label="Partita IVA"
            defaultValue="0123456789"
            error="La partita IVA deve essere composta da 11 cifre."
          />
          <TextField label="PEC" helper="La usiamo solo per gli avvisi ufficiali." />
          <TextField label="Codice fiscale" value="RNLGLI80A01L219X" disabled readOnly />
          <SelectField label="Forma giuridica" defaultValue="srl">
            <option value="srl">S.r.l.</option>
            <option value="spa">S.p.A.</option>
            <option value="snc">S.n.c.</option>
          </SelectField>
          <SelectField label="Regione" error="Scegli una regione." defaultValue="">
            <option value="">Scegli…</option>
            <option value="piemonte">Piemonte</option>
          </SelectField>
          <div className="sm:col-span-2">
            <TextareaField
              label="Descrizione dell'attività"
              helper="Massimo 500 caratteri."
              placeholder="Che cosa fa l'azienda, in due righe."
            />
          </div>
        </div>
      </Blocco>

      <Blocco titolo="Dialog" nota="Foglio con raggio panel e shadow-overlay sopra il velo; la X è un IconButton.">
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => setDialogAperto(true)}>
            Apri la finestra
          </Button>
          <Button variant="secondary" onClick={() => setDialogBloccato(true)}>
            Apri la finestra non chiudibile
          </Button>
        </div>
        <Dialog
          open={dialogAperto}
          onClose={() => setDialogAperto(false)}
          title="Rimuovere l'azienda dalle aziende gestite?"
          footer={
            <>
              <Button variant="secondary" onClick={() => setDialogAperto(false)}>
                Annulla
              </Button>
              <Button variant="danger" onClick={() => setDialogAperto(false)}>
                Rimuovi
              </Button>
            </>
          }
        >
          <p>
            <strong className="text-ink">Fonderia Bertolotti S.r.l.</strong> esce dal selettore
            dell'azienda, dagli avvisi e dagli export. I suoi dati (bandi salvati, calendario,
            AI-check, dossier) restano conservati.
          </p>
        </Dialog>
        <Dialog
          open={dialogBloccato}
          onClose={() => setDialogBloccato(false)}
          title="Pagamento in corso"
          dismissible={false}
          size="lg"
          footer={
            <Button onClick={() => setDialogBloccato(false)}>Ho capito</Button>
          }
        >
          <p>Non chiudere questa finestra finché il pagamento non è confermato.</p>
        </Dialog>
      </Blocco>

      <Blocco titolo="Stati" nota="Skeleton su sunken senza animazione; vuoto ed errore a sinistra.">
        <div className="grid gap-6 lg:grid-cols-2">
          <div className="flex flex-col gap-2">
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-1/2" />
          </div>
          <EmptyState
            title="Non hai ancora salvato nessun bando."
            description="Sfoglia i bandi e usa il segnalibro per mettere da parte quelli che ti interessano: li ritrovi qui."
            action={
              <LinkButton to="/app/bandi" variant="secondary">
                Cerca nei bandi
              </LinkButton>
            }
          />
          <ErrorState
            title="Non siamo riusciti a caricare i bandi salvati."
            message="Il servizio non ha risposto in tempo. Riprova tra qualche istante: i bandi che hai salvato non sono andati persi."
            onRetry={() => undefined}
          />
          <ErrorState
            title="Questo bando non è più disponibile."
            message="L'ente che lo aveva pubblicato lo ha ritirato: puoi cercarne altri nell'elenco."
          />
        </div>
      </Blocco>

      <Blocco titolo="Pagination" nota="«Pagina N di M» a sinistra, Precedente e Successiva a destra.">
        <Pagination page={pagina} totalPages={9} onChange={setPagina} />
        <Pagination page={1} totalPages={1} onChange={setPagina} />
      </Blocco>

      <Blocco titolo="Menu" nota="Trigger di sola icona; voci da 36px, distruttiva e disabilitata.">
        <div className="flex items-center gap-4">
          <Menu label="Altre azioni">
            <MenuItem icon={<Pencil />} onSelect={() => undefined}>
              Modifica
            </MenuItem>
            <MenuItem disabled title="Non puoi duplicare una call chiusa">
              Duplica
            </MenuItem>
            <MenuSeparator />
            <MenuItem danger icon={<Trash2 />} onSelect={() => undefined}>
              Elimina
            </MenuItem>
          </Menu>
          <span className="text-small text-ink-3">Apri con il clic o con le frecce.</span>
        </div>
      </Blocco>

      <Blocco titolo="Combobox e TagSelect" nota="Select con ricerca e multi-selezione con valori ereditati.">
        <div className="grid max-w-3xl gap-4 sm:grid-cols-2">
          <Combobox
            label="Regione"
            options={REGIONI}
            value={regione}
            onChange={setRegione}
            helper="Scrivi per filtrare l'elenco."
          />
          <Combobox
            label="Regione (con errore)"
            options={REGIONI}
            value={null}
            onChange={() => undefined}
            error="Scegli una regione."
            required
          />
          <Combobox
            label="Regione (disabilitata)"
            options={REGIONI}
            value={1}
            onChange={() => undefined}
            disabled
          />
          <div className="flex flex-col gap-1.5">
            <p className="text-small font-medium text-ink">Settori di interesse</p>
            <TagSelect
              label="Settori di interesse"
              options={REGIONI}
              values={settori}
              inherited={[5]}
              onToggle={(id) =>
                setSettori((v) => (v.includes(id) ? v.filter((x) => x !== id) : [...v, id]))
              }
            />
            <p className="text-small text-ink-3">
              Scelti: {settori.length === 0 ? "nessuno" : settori.join(", ")}
            </p>
          </div>
        </div>
      </Blocco>
    </div>
  );
}
