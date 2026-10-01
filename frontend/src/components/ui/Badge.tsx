import type { HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

type Tone = "brand" | "emerald" | "amber" | "slate" | "red";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  /** Accettato per compatibilità, non colora più: lo stato in parole lo fa `Status`. */
  tone?: Tone;
}

/** Etichetta neutra (il `Tag` delle tavole): fondo `sunken`, testo `ink-2`,
 *  raggio `mark`, 12px 500. Qualunque tono, lo stesso aspetto. */
export function Badge({ tone: _tone = "slate", className, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex min-h-6 items-center gap-1 rounded-mark bg-sunken px-2 py-0.5 text-caption text-ink-2",
        className,
      )}
      {...props}
    />
  );
}
