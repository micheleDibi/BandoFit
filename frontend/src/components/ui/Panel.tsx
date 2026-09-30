import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface PanelProps extends HTMLAttributes<HTMLElement> {
  titolo?: ReactNode;
  /** A destra del titolo (es. il `Fit` di «Fa per te?»). */
  azione?: ReactNode;
  children: ReactNode;
}

/** Pannello su `desk` per un blocco di servizio accanto al contenuto (colonna
 *  laterale). Mai un riquadro dentro un riquadro. */
export function Panel({ titolo, azione, className, children, ...props }: PanelProps) {
  return (
    <section className={cn("flex flex-col gap-3 rounded-panel bg-desk p-5", className)} {...props}>
      {(titolo || azione) && (
        <div className="flex items-center justify-between gap-4">
          {titolo && <h3 className="font-sans text-title-group text-ink">{titolo}</h3>}
          {azione && <div className="ml-auto shrink-0">{azione}</div>}
        </div>
      )}
      {children}
    </section>
  );
}
