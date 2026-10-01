import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "../../lib/cn";

/** Toni dello stato: una pillola colorata con un pallino e la parola. */
export type TonoStatus =
  | "aperto"
  | "in-apertura"
  | "in-scadenza"
  | "chiuso"
  | "attenzione"
  | "errore"
  | "neutro";

// Fondo soft e testo ink del tono: aperto → fit, in apertura → accent, in
// scadenza → warm, attenzione → warning, errore → danger, chiuso e neutro →
// neutral. Ogni coppia ≥ 4,6:1.
const pillola: Record<TonoStatus, string> = {
  aperto: "bg-fit-soft text-fit-ink",
  "in-apertura": "bg-accent-soft text-accent-hover",
  "in-scadenza": "bg-warm-soft text-warm-ink",
  chiuso: "bg-neutral-soft text-neutral-ink",
  attenzione: "bg-warning-soft text-warning-ink",
  errore: "bg-danger-soft text-danger",
  neutro: "bg-neutral-soft text-neutral-ink",
};

// Il pallino nel colore pieno del tono. La forma aiuta a non contare solo sul
// colore: pieno per gli stati attivi, vuoto (solo anello) per in apertura e
// chiuso, quadrato per attenzione ed errore.
const punto: Record<TonoStatus, string> = {
  aperto: "rounded-pill bg-fit",
  "in-apertura": "rounded-pill border-2 border-accent",
  "in-scadenza": "rounded-pill bg-warm",
  chiuso: "rounded-pill border-2 border-ink-off",
  attenzione: "rounded-xs bg-warning",
  errore: "rounded-xs bg-danger",
  neutro: "rounded-pill bg-ink-off",
};

export interface StatusProps extends HTMLAttributes<HTMLSpanElement> {
  tono: TonoStatus;
  children: ReactNode;
}

/** Stato in parole («Aperto», «In apertura», «Chiuso»…): pillola nel colore del
 *  tono, pallino e parola. Una riga ha al massimo uno stato. */
export function Status({ tono, className, children, ...props }: StatusProps) {
  return (
    <span
      className={cn(
        "inline-flex min-h-6 items-center gap-1.5 whitespace-nowrap rounded-pill px-2.5 text-small font-medium",
        pillola[tono],
        className,
      )}
      {...props}
    >
      <span aria-hidden className={cn("size-2 shrink-0", punto[tono])} />
      {children}
    </span>
  );
}
