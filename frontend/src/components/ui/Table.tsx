import type { TableHTMLAttributes, TdHTMLAttributes, ThHTMLAttributes } from "react";
import { cn } from "../../lib/cn";

export interface TableProps extends TableHTMLAttributes<HTMLTableElement> {
  /** Classi del contenitore che scorre in orizzontale sotto la larghezza della tabella. */
  classNameContenitore?: string;
}

/** Tabella: testata 13px `ink-3` con bordo `line-control`, righe con filetto `line`.
 *  Si compone con `Th` e `Td`; `numerica` allinea a destra. */
export function Table({ className, classNameContenitore, children, ...props }: TableProps) {
  return (
    <div className={cn("overflow-x-auto", classNameContenitore)}>
      <table className={cn("w-full border-collapse text-body text-ink", className)} {...props}>
        {children}
      </table>
    </div>
  );
}

interface CellaProps {
  /** Colonna di numeri: allineata a destra (e cifre tabellari nelle celle). */
  numerica?: boolean;
}

export function Th({
  numerica,
  scope = "col",
  className,
  ...props
}: ThHTMLAttributes<HTMLTableCellElement> & CellaProps) {
  return (
    <th
      scope={scope}
      className={cn(
        "whitespace-nowrap border-b border-line-control px-3 py-2.5 text-left text-small font-medium text-ink-3",
        numerica && "text-right",
        className,
      )}
      {...props}
    />
  );
}

export function Td({
  numerica,
  className,
  ...props
}: TdHTMLAttributes<HTMLTableCellElement> & CellaProps) {
  return (
    <td
      className={cn(
        "border-b border-line px-3 py-3.5 align-top",
        numerica && "text-right tabular-nums",
        className,
      )}
      {...props}
    />
  );
}
