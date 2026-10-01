import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { cn } from "../../lib/cn";
import { formatEur } from "../../lib/format";
import type { BandoListItem } from "../../types";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";
import { Due, statoScadenza } from "../ui/Due";
import { Fit } from "../ui/Fit";
import { Skeleton } from "../ui/states";
import { StatoBadge } from "./badges";
import { SaveBandoButton } from "./SaveBandoButton";
import { bandoInCorso, statoDelBando } from "./stato";

/** Importo della riga: la dotazione se c'è, altrimenti il contributo massimo
 *  (stessa etichetta della scheda del bando). */
function importoRiga(b: BandoListItem): { valore: string; nota: string } | null {
  if (b.importo_totale_eur !== null) {
    return { valore: formatEur(b.importo_totale_eur), nota: "dotazione" };
  }
  if (b.importo_max_per_progetto_eur !== null) {
    return { valore: formatEur(b.importo_max_per_progetto_eur), nota: "contributo massimo" };
  }
  return null;
}

function Importo({ importo, className }: { importo: ReturnType<typeof importoRiga>; className?: string }) {
  if (!importo) {
    return <span className={className}>Importo non indicato</span>;
  }
  return (
    <span className={className}>
      <span className="text-figure-sm text-ink">{importo.valore}</span>
      <span className="text-small text-ink-3">{importo.nota}</span>
    </span>
  );
}

/** Bordo sinistro della card per stato: aperto fit, in apertura accent, in
 *  scadenza (entro 7 giorni, bando in corso) warm, chiuso o altro neutro. Il
 *  colore non è mai solo: lo stato è scritto (`StatoBadge`) e la scadenza ha il
 *  tempo relativo (`Due`). */
type Bordo = "aperto" | "in-apertura" | "in-scadenza" | "neutro";

const bordi: Record<Bordo, string> = {
  aperto: "border-l-4 border-l-fit",
  "in-apertura": "border-l-4 border-l-accent",
  "in-scadenza": "border-l-4 border-l-warm",
  neutro: "border-l-4 border-l-ink-off",
};

function bordoDelBando(stato: string | null, scadenza: string | null): Bordo {
  if (stato === null || !bandoInCorso(stato)) return "neutro";
  if (statoScadenza(scadenza) === "urgente") return "in-scadenza";
  return stato === "aperto" ? "aperto" : "in-apertura";
}

/** Card di un bando nel registro (tavola `Main`): scadenza a tessera, titolo
 *  con il riassunto, stato in parole + ente + tipologia, importo in evidenza,
 *  compatibilità e segnalibro. Il titolo è il link alla scheda e copre la card
 *  intera (`after:absolute`): il segnalibro e le `azioni` stanno sopra
 *  (`relative z-10`), fratelli del link e mai dentro. Sotto `md` importo e
 *  compatibilità passano sotto il testo (tavola `MobileBandi`). `azioni`:
 *  pulsanti testuali sotto i metadati (es. la scadenza in calendario nei bandi
 *  salvati). Resta un `<li>`: va dentro un `<ul>`. */
export function BandoRow({ bando, azioni }: { bando: BandoListItem; azioni?: ReactNode }) {
  // Stesso titolo del dettaglio: si preferisce quello esteso (più specifico),
  // con titolo_breve come ripiego (BandoDetail.tsx usa lo stesso ordine).
  const titolo = bando.titolo ?? bando.titolo_breve ?? "Bando senza titolo";
  const stato = statoDelBando(bando);
  const importo = importoRiga(bando);
  const fit = bando.compatibilita ? (
    <Fit soddisfatti={bando.compatibilita.matched} totale={bando.compatibilita.totale} />
  ) : null;

  return (
    <li>
      <Card
        interattiva
        className={cn(
          "relative flex items-start gap-4 p-4 md:gap-6 md:p-5",
          bordi[bordoDelBando(stato, bando.data_scadenza)],
        )}
      >
        {/* Il conto alla rovescia solo a bando aperto o in apertura. */}
        <Due data={bando.data_scadenza} conConto={bandoInCorso(stato)} />

        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <Link
            to={`/app/bandi/${bando.slug}`}
            className={cn(
              "self-start rounded-mark text-row-title text-ink hover:text-accent-hover",
              // Il link copre la card: l'anello del focus si disegna sulla card intera.
              // (`outline-solid` sul ::after: lo stile `none` del link passerebbe per variabile.)
              "after:absolute after:inset-0 after:rounded-panel",
              "focus-visible:outline-none focus-visible:after:outline-solid focus-visible:after:outline-2",
              "focus-visible:after:outline-offset-2 focus-visible:after:outline-accent",
            )}
          >
            {titolo}
          </Link>
          {bando.descrizione_breve && (
            <p className="line-clamp-2 text-body text-ink-2">{bando.descrizione_breve}</p>
          )}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-small text-ink-2">
            <StatoBadge stato={stato} daVerificare={bando.stato_da_verificare} />
            {bando.ente_erogatore && <span>{bando.ente_erogatore}</span>}
            {bando.tipologia && <Badge area="bandi">{bando.tipologia.nome}</Badge>}
          </div>
          {/* Sotto md: importo e compatibilità in una riga sotto il testo. */}
          <div className="mt-1 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 md:hidden">
            <Importo importo={importo} className="inline-flex items-baseline gap-1.5 text-small text-ink-3" />
            {fit}
          </div>
          {azioni && (
            <div className="relative z-10 mt-1 flex flex-wrap items-center gap-2">{azioni}</div>
          )}
        </div>

        <Importo
          importo={importo}
          className="hidden w-36 shrink-0 flex-col items-end gap-0.5 self-center border-l border-line pl-5 text-right text-small text-ink-3 md:flex"
        />
        <div className="hidden w-30 shrink-0 self-center md:flex">{fit}</div>

        <div className="relative z-10 shrink-0">
          <SaveBandoButton bando={{ id: bando.id, slug: bando.slug }} variant="riga" />
        </div>
      </Card>
    </li>
  );
}

/** Tre card del registro mentre arrivano i dati, con le stesse colonne. */
export function BandoRowSkeleton() {
  return (
    <li aria-hidden>
      <Card className="flex items-start gap-4 p-4 md:gap-6 md:p-5">
        <Skeleton className="h-16 w-18 shrink-0 rounded-control" />
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <Skeleton className="h-5 w-3/5" />
          <Skeleton className="h-4 w-4/5" />
          <div className="flex gap-3">
            <Skeleton className="h-5 w-16 rounded-pill" />
            <Skeleton className="h-5 w-24 rounded-pill" />
            <Skeleton className="h-3 w-20 self-center" />
          </div>
        </div>
        <div className="hidden w-36 shrink-0 flex-col items-end gap-1.5 self-center md:flex">
          <Skeleton className="h-5 w-20" />
          <Skeleton className="h-3 w-14" />
        </div>
        <div className="hidden w-30 shrink-0 self-center md:flex">
          <Skeleton className="h-4 w-16" />
        </div>
        <Skeleton className="size-10 shrink-0" />
      </Card>
    </li>
  );
}
