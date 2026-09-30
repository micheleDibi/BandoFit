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

/** Sezioni richiudibili su `<details>` nativo, con `summary` uniforme e filetti. */
export function Accordion({ items, className }: AccordionProps) {
  return (
    <div className={cn("divide-y divide-line border-y border-line", className)}>
      {items.map((voce) => (
        <details key={voce.id} id={voce.id} open={voce.aperto} className="group">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-4 rounded-mark py-3 text-title-group text-ink [&::-webkit-details-marker]:hidden">
            {voce.titolo}
            <ChevronDown
              className="size-4 shrink-0 text-ink-2 group-open:rotate-180"
              strokeWidth={1.75}
              aria-hidden
            />
          </summary>
          <div className="pb-4 text-body text-ink-2">{voce.children}</div>
        </details>
      ))}
    </div>
  );
}
