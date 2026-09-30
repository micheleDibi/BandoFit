import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface SectionHeaderProps {
  titolo: ReactNode;
  /** A destra del titolo, sulla stessa linea di base (link o pulsante testuale). */
  azione?: ReactNode;
  /** Id del titolo (ancore come `#ai-check-report`). */
  id?: string;
  /** Livello del titolo: 2 di default, 3 dentro una sezione. */
  livello?: 2 | 3;
  className?: string;
}

/** Titolo di sezione sopra un filetto: le sezioni si separano così, non con un riquadro. */
export function SectionHeader({ titolo, azione, id, livello = 2, className }: SectionHeaderProps) {
  const Titolo = livello === 3 ? "h3" : "h2";
  return (
    <div
      className={cn(
        "flex items-baseline justify-between gap-4 border-b border-line pb-3",
        className,
      )}
    >
      <Titolo id={id} className="text-title-section text-ink">
        {titolo}
      </Titolo>
      {azione && <div className="shrink-0">{azione}</div>}
    </div>
  );
}

/** Contenitore di una sezione: `SectionHeader` e contenuto con gap 16. */
export function Section({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return <section className={cn("flex flex-col gap-4", className)} {...props} />;
}
