import { useEffect, useState, type ReactNode } from "react";
import { apiErrorMessage } from "../../../lib/api";
import { cn } from "../../../lib/cn";
import { Alert } from "../../ui/Alert";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { Dialog } from "../../ui/Dialog";
import { SelectField } from "../../ui/Field";
import { EmptyState, ErrorState, Skeleton } from "../../ui/states";
import { Table } from "../../ui/Table";
import { TestoLungo } from "../CampiCall";

/** Pezzi comuni delle schede del pannello admin dei partenariati (WP9). */

/** Filtro a tendina con etichetta visibile (niente solo `aria-label`). */
export function Filtro<T extends string>({
  etichetta,
  valore,
  onChange,
  opzioni,
}: {
  etichetta: string;
  valore: T | "";
  onChange: (valore: T | "") => void;
  opzioni: Array<{ valore: T | ""; etichetta: string }>;
}) {
  return (
    <div className="min-w-44">
      <SelectField
        label={etichetta}
        value={valore}
        onChange={(e) => onChange(e.target.value as T | "")}
      >
        {opzioni.map((o) => (
          <option key={o.valore || "tutti"} value={o.valore}>
            {o.etichetta}
          </option>
        ))}
      </SelectField>
    </div>
  );
}

/** Stati di una lista: caricamento, errore con «Riprova», vuoto, dati (che
 *  restano visibili in trasparenza durante il cambio pagina o filtro). */
export function StatiLista({
  isPending,
  isError,
  error,
  onRetry,
  vuoto,
  titoloVuoto,
  descrizioneVuoto,
  children,
}: {
  isPending: boolean;
  isError: boolean;
  error: unknown;
  onRetry: () => void;
  vuoto: boolean;
  titoloVuoto: string;
  descrizioneVuoto?: string;
  children: ReactNode;
}) {
  if (isPending) {
    return (
      <div className="flex flex-col gap-3" aria-hidden>
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-16 w-full rounded-control" />
        ))}
      </div>
    );
  }
  if (isError) return <ErrorState message={apiErrorMessage(error)} onRetry={onRetry} />;
  // Il vuoto su una Card, con l'icona dell'area admin (come le altre pagine Admin).
  if (vuoto)
    return (
      <Card>
        <EmptyState title={titoloVuoto} description={descrizioneVuoto} area="admin" />
      </Card>
    );
  return <>{children}</>;
}

/** Riga di stato per gli esiti delle azioni (sempre montata: l'annuncio
 *  arriva anche se il bottone che l'ha causato sparisce). */
export function Annuncio({ testo }: { testo: string | null }) {
  return (
    <div role="status" aria-live="polite">
      {/* La regione live è il contenitore: l'avviso non ne apre un'altra. */}
      {testo && (
        <Alert tono="ok" ruolo="none">
          {testo}
        </Alert>
      )}
    </div>
  );
}

/** Dialog con una motivazione obbligatoria (sospensione, ripristino,
 *  revoca, contesto completo): il testo va da `min` a `max` caratteri e si
 *  conferma con un bottone esplicito. */
export function MotivazioneDialog({
  open,
  onClose,
  titolo,
  descrizione,
  etichetta,
  min,
  max,
  etichettaConferma,
  pericolosa = false,
  inCorso,
  errore,
  onConferma,
  children,
}: {
  open: boolean;
  onClose: () => void;
  titolo: string;
  descrizione?: ReactNode;
  etichetta: string;
  min: number;
  max: number;
  etichettaConferma: string;
  pericolosa?: boolean;
  inCorso: boolean;
  /** Errore del server (già tradotto in messaggio). */
  errore: string | null;
  onConferma: (testo: string) => void;
  children?: ReactNode;
}) {
  const [testo, setTesto] = useState("");
  const [erroreLocale, setErroreLocale] = useState<string | null>(null);

  // A ogni apertura si riparte da zero.
  useEffect(() => {
    if (!open) return;
    setTesto("");
    setErroreLocale(null);
  }, [open]);

  const conferma = () => {
    const pulito = testo.trim();
    if (pulito.length < min) {
      setErroreLocale(min <= 1 ? "Scrivi il motivo." : `Scrivi almeno ${min} caratteri.`);
      return;
    }
    setErroreLocale(null);
    onConferma(pulito);
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={titolo}
      dismissible={!inCorso}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={inCorso}>
            Annulla
          </Button>
          <Button variant={pericolosa ? "danger" : "primary"} loading={inCorso} onClick={conferma}>
            {etichettaConferma}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {descrizione && <div className="text-ink">{descrizione}</div>}
        {children}
        <TestoLungo
          etichetta={etichetta}
          aiuto={min > 1 ? `Da ${min} a ${max} caratteri.` : `Al massimo ${max} caratteri.`}
          valore={testo}
          onChange={setTesto}
          massimo={max}
          righe={4}
          required
          errore={erroreLocale ?? undefined}
        />
        {errore && <Alert tono="errore">{errore}</Alert>}
      </div>
    </Dialog>
  );
}

/** Tabella admin (`Table` di ui) su una Card con ombra, come nelle altre
 *  pagine Admin: scorrimento orizzontale sugli schermi stretti, nessun
 *  filetto sotto l'ultima riga (lo chiude il bordo della card) e i dati
 *  attenuati durante il cambio di pagina o filtro. */
export function TabellaCard({
  caption,
  attenuata,
  children,
}: {
  caption: string;
  attenuata?: boolean;
  children: ReactNode;
}) {
  return (
    <Card className="overflow-hidden p-0">
      <Table
        className={cn(
          "min-w-[760px] [&_tbody_tr:last-child>*]:border-b-0",
          attenuata && "opacity-60 transition-opacity",
        )}
        classNameContenitore="px-2"
        aria-busy={attenuata}
      >
        <caption className="sr-only">{caption}</caption>
        {children}
      </Table>
    </Card>
  );
}

/** Intestazione di riga (`th scope="row"`) con l'aspetto di una cella. */
export const thRigaClass = "border-b border-line px-3 py-3.5 text-left align-top font-normal";

/** Numero in italiano (separatore delle migliaia, decimali se servono). */
export function numero(valore: number | null | undefined, decimali = 0): string {
  if (valore === null || valore === undefined || !Number.isFinite(valore)) return "—";
  return new Intl.NumberFormat("it-IT", {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimali,
  }).format(valore);
}

/** Importo in centesimi nella valuta indicata (EUR o USD), mai convertito. */
export function importoCents(cents: number, valuta: string): string {
  return new Intl.NumberFormat("it-IT", { style: "currency", currency: valuta }).format(cents / 100);
}
