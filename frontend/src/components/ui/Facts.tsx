import { Fragment, type ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface Fatto {
  etichetta: ReactNode;
  valore: ReactNode;
  /** Accanto al valore, in piccolo (es. il tempo relativo della scadenza). */
  nota?: ReactNode;
}

export interface FactsProps {
  items: Fatto[];
  /** A destra, allineata ai fatti (es. «Aggiungi la scadenza al calendario»). */
  azione?: ReactNode;
  className?: string;
}

/** Fatti chiave in riga, con filetti sopra e sotto: etichetta sopra, valore sotto. */
export function Facts({ items, azione, className }: FactsProps) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-10 gap-y-4 border-y border-line py-4",
        className,
      )}
    >
      <dl className="flex flex-wrap gap-x-10 gap-y-4">
        {items.map((fatto, i) => (
          <div key={i} className="flex flex-col gap-0.5">
            <dt className="text-small text-ink-3">{fatto.etichetta}</dt>
            <dd className="flex flex-wrap items-baseline gap-2 text-figure-sm text-ink">
              {fatto.valore}
              {fatto.nota && <span className="font-sans text-caption text-ink-3">{fatto.nota}</span>}
            </dd>
          </div>
        ))}
      </dl>
      {azione && <div className="ml-auto shrink-0">{azione}</div>}
    </div>
  );
}

export interface Definizione {
  etichetta: ReactNode;
  valore: ReactNode;
  /** Dopo il valore, in secondo piano (es. la descrizione del codice ATECO). */
  nota?: ReactNode;
}

export interface DefinitionListProps {
  items: Definizione[];
  className?: string;
}

/** Elenco etichetta/valore a due colonne (200px / resto); una colonna sotto `sm`. */
export function DefinitionList({ items, className }: DefinitionListProps) {
  return (
    <dl
      className={cn(
        "grid grid-cols-1 gap-x-6 gap-y-1 text-body sm:grid-cols-[200px_minmax(0,1fr)] sm:gap-y-2.5",
        className,
      )}
    >
      {items.map((voce, i) => (
        <Fragment key={i}>
          <dt className="text-ink-3 not-first:mt-2 sm:mt-0">{voce.etichetta}</dt>
          <dd className="min-w-0 text-ink">
            {voce.valore}
            {voce.nota && <span className="ml-3 text-ink-2">{voce.nota}</span>}
          </dd>
        </Fragment>
      ))}
    </dl>
  );
}
