import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "../../lib/cn";
import type { Area } from "./area";
import { IconChip, type IconaChip } from "./IconChip";

export interface PanelProps extends HTMLAttributes<HTMLElement> {
  titolo?: ReactNode;
  /** A destra del titolo (es. il `Fit` di «Fa per te?»). */
  azione?: ReactNode;
  /** Icona nell'intestazione, in un `IconChip` piccolo prima del titolo. */
  icon?: IconaChip;
  /** Colore dell'`IconChip` (default il navy di `home`). */
  area?: Area;
  children: ReactNode;
}

/** Pannello su foglio bianco con ombra `card` per un blocco di servizio accanto
 *  al contenuto (colonna laterale). Mai un riquadro dentro un riquadro. */
export function Panel({ titolo, azione, icon, area, className, children, ...props }: PanelProps) {
  return (
    <section
      className={cn(
        "flex flex-col gap-3 rounded-panel border border-line bg-sheet p-5 shadow-card",
        className,
      )}
      {...props}
    >
      {(titolo || azione) && (
        <div className="flex items-center justify-between gap-4">
          {titolo && (
            <div className="flex min-w-0 items-center gap-2.5">
              {icon && <IconChip icon={icon} area={area} size="sm" />}
              <h3 className="font-sans text-title-group text-ink">{titolo}</h3>
            </div>
          )}
          {azione && <div className="ml-auto shrink-0">{azione}</div>}
        </div>
      )}
      {children}
    </section>
  );
}
