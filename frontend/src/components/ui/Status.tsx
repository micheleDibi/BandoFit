import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "../../lib/cn";

/** Toni dello stato: una parola con un punto, mai un badge colorato. */
export type TonoStatus = "aperto" | "in-apertura" | "chiuso" | "attenzione" | "neutro";

const testo: Record<TonoStatus, string> = {
  aperto: "text-fit-ink",
  "in-apertura": "text-warning-ink",
  chiuso: "text-ink-2",
  attenzione: "text-danger",
  neutro: "text-ink-2",
};

// Il punto: pieno per aperto/neutro/attenzione, vuoto (solo anello) per in
// apertura e chiuso; quadrato per l'attenzione così non conta solo il colore.
const punto: Record<TonoStatus, string> = {
  aperto: "rounded-pill bg-fit",
  "in-apertura": "rounded-pill border-2 border-warning-ink",
  chiuso: "rounded-pill border-2 border-ink-off",
  attenzione: "rounded-xs bg-danger",
  neutro: "rounded-pill bg-ink-off",
};

export interface StatusProps extends HTMLAttributes<HTMLSpanElement> {
  tono: TonoStatus;
  children: ReactNode;
}

/** Stato in parole («Aperto», «In apertura», «Chiuso»…): punto + parola. */
export function Status({ tono, className, children, ...props }: StatusProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap text-small font-medium",
        testo[tono],
        className,
      )}
      {...props}
    >
      <span aria-hidden className={cn("size-2 shrink-0", punto[tono])} />
      {children}
    </span>
  );
}
