import { ChevronDown } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface VoceAccordion {
  /** Id del `<details>` (le ancore lette dal codice restano nel DOM). */
  id: string;
  titolo: ReactNode;
  children: ReactNode;
  /** Aperta al primo render (poi decide l'utente). */
  aperto?: boolean;
}

export interface AccordionProps {
  items: VoceAccordion[];
  className?: string;
}

/** Sezioni richiudibili su `<details>` nativo, con `summary` uniforme e filetti.
 *  L'apertura è animata (righe della griglia da 0fr a 1fr, regola
 *  `.accordion-voce` in `index.css`) e il chevron ruota; con il movimento
 *  ridotto si apre di scatto. */
export function Accordion({ items, className }: AccordionProps) {
  return (
    <div className={cn("divide-y divide-line border-y border-line", className)}>
      {items.map((voce) => (
        <details key={voce.id} id={voce.id} open={voce.aperto} className="accordion-voce group">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-4 rounded-mark py-3 text-title-group text-ink transition-colors duration-150 hover:text-accent-hover [&::-webkit-details-marker]:hidden">
            {voce.titolo}
            <ChevronDown
              className="size-4 shrink-0 text-ink-2 group-open:rotate-180 motion-safe:transition-transform motion-safe:duration-250 motion-safe:ease-uscita"
              strokeWidth={1.75}
              aria-hidden
            />
          </summary>
          {/* Figlio unico della griglia: `min-h-0` lo lascia stringere a zero, il
              ritaglio solo in verticale non taglia gli anelli del focus ai lati. */}
          <div className="min-h-0 overflow-y-clip">
            <div className="pb-4 text-body text-ink-2">{voce.children}</div>
          </div>
        </details>
      ))}
    </div>
  );
}
