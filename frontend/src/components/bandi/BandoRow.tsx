import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { formatEur } from "../../lib/format";
import type { BandoListItem } from "../../types";
import { Due } from "../ui/Due";
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

/** Riga del registro dei bandi (tavola `Main`): scadenza, titolo con il
 *  riassunto in una riga, stato in parole + ente + tipologia, importo,
 *  compatibilità e segnalibro. Il titolo è il link alla scheda; il segnalibro
 *  è un fratello del link, mai dentro. Sotto `md` importo e compatibilità
 *  passano sotto il testo (tavola `MobileBandi`). `azioni`: pulsanti testuali
 *  sotto i metadati (es. la scadenza in calendario nei bandi salvati). */
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
    <li className="flex items-start gap-4 border-b border-line px-2 py-4 md:gap-6">
      {/* Il conto alla rovescia solo a bando aperto o in apertura. */}
      <Due data={bando.data_scadenza} conConto={bandoInCorso(stato)} />

      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <Link
          to={`/app/bandi/${bando.slug}`}
          className="self-start rounded-mark text-row-title text-ink hover:text-accent-hover"
        >
          {titolo}
        </Link>
        {bando.descrizione_breve && (
          <p className="line-clamp-1 text-body text-ink-2">{bando.descrizione_breve}</p>
        )}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-small text-ink-2">
          <StatoBadge stato={stato} />
          {bando.ente_erogatore && <span>{bando.ente_erogatore}</span>}
          {bando.tipologia && <span>{bando.tipologia.nome}</span>}
        </div>
        {/* Sotto md: importo e compatibilità in una riga sotto il testo. */}
        <div className="mt-1 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 md:hidden">
          <Importo importo={importo} className="inline-flex items-baseline gap-1.5 text-small text-ink-3" />
          {fit}
        </div>
        {azioni && <div className="mt-1 flex flex-wrap items-center gap-2">{azioni}</div>}
      </div>

      <Importo
        importo={importo}
        className="hidden w-33 shrink-0 flex-col items-end gap-0.5 text-right text-small text-ink-3 md:flex"
      />
      <div className="hidden w-30 shrink-0 md:flex">{fit}</div>

      <SaveBandoButton bando={{ id: bando.id, slug: bando.slug }} variant="riga" />
    </li>
  );
}

/** Tre righe del registro mentre arrivano i dati, con le stesse colonne. */
export function BandoRowSkeleton() {
  return (
    <li className="flex items-start gap-4 border-b border-line px-2 py-4 md:gap-6" aria-hidden>
      <div className="flex w-18 shrink-0 flex-col gap-1.5">
        <Skeleton className="h-7 w-8" />
        <Skeleton className="h-3 w-14" />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <Skeleton className="h-5 w-3/5" />
        <Skeleton className="h-4 w-4/5" />
        <div className="flex gap-3">
          <Skeleton className="h-3 w-14" />
          <Skeleton className="h-3 w-28" />
          <Skeleton className="h-3 w-20" />
        </div>
      </div>
      <div className="hidden w-33 shrink-0 flex-col items-end gap-1.5 md:flex">
        <Skeleton className="h-5 w-20" />
        <Skeleton className="h-3 w-14" />
      </div>
      <div className="hidden w-30 shrink-0 md:flex">
        <Skeleton className="h-4 w-16" />
      </div>
      <Skeleton className="size-10 shrink-0" />
    </li>
  );
}
