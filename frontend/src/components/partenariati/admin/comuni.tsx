import { useEffect, useState, type ReactNode } from "react";
import { apiErrorMessage } from "../../../lib/api";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { Dialog } from "../../ui/Dialog";
import { EmptyState, ErrorState, Skeleton } from "../../ui/states";
import { TestoLungo } from "../CampiCall";

/** Pezzi comuni delle schede del pannello admin dei partenariati (WP9). */

export const thClass = "px-4 py-3 font-medium";

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
    <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
      {etichetta}
      <select
        value={valore}
        onChange={(e) => onChange(e.target.value as T | "")}
        className="h-10 w-full min-w-44 cursor-pointer rounded-lg border border-slate-300 bg-white px-3 text-sm font-normal text-slate-900 focus:border-brand-500 focus:outline-2 focus:outline-offset-0 focus:outline-brand-500/30"
      >
        {opzioni.map((o) => (
          <option key={o.valore || "tutti"} value={o.valore}>
            {o.etichetta}
          </option>
        ))}
      </select>
    </label>
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
      <div className="space-y-3" aria-hidden>
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }
  if (isError) return <ErrorState message={apiErrorMessage(error)} onRetry={onRetry} />;
  if (vuoto) return <EmptyState title={titoloVuoto} description={descrizioneVuoto} />;
  return <>{children}</>;
}

/** Riga di stato per gli esiti delle azioni (sempre montata: l'annuncio
 *  arriva anche se il bottone che l'ha causato sparisce). */
export function Annuncio({ testo }: { testo: string | null }) {
  return (
    <div role="status" aria-live="polite">
      {testo && (
        <p className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{testo}</p>
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
          <Button variant="ghost" onClick={onClose} disabled={inCorso}>
            Annulla
          </Button>
          <Button variant={pericolosa ? "danger" : "primary"} loading={inCorso} onClick={conferma}>
            {etichettaConferma}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {descrizione && <div className="text-sm text-slate-700">{descrizione}</div>}
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
        {errore && (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {errore}
          </p>
        )}
      </div>
    </Dialog>
  );
}

/** Tabella admin su Card, con scorrimento orizzontale sugli schermi stretti. */
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
      <div className="overflow-x-auto">
        <table
          className={`w-full min-w-[760px] text-left text-sm ${attenuata ? "opacity-60 transition-opacity" : ""}`}
          aria-busy={attenuata}
        >
          <caption className="sr-only">{caption}</caption>
          {children}
        </table>
      </div>
    </Card>
  );
}

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
