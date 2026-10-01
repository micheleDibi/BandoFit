import type { HTMLAttributes } from "react";
import { cn } from "../../lib/cn";
import { areaClassi, type Area } from "./area";

/** Toni dell'etichetta. I nomi vecchi (`brand`, `emerald`, `amber`, `slate`,
 *  `red`) restano accettati e valgono come il tono nuovo corrispondente. */
export type BadgeTone =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger"
  | "warm"
  | "brand"
  | "emerald"
  | "amber"
  | "slate"
  | "red";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  /** Colore dell'etichetta (default `neutral`). Ogni tono ha la sua parola: il
   *  testo dell'etichetta dice che cosa significa, il colore la fa trovare. */
  tone?: BadgeTone;
  /** Etichetta nel colore di un'area (fondo soft, testo ink): vince su `tone`. */
  area?: Area;
}

// Fondo soft e testo ink: ogni coppia ≥ 4,5:1 (docs/design-system.md).
const toni: Record<BadgeTone, string> = {
  neutral: "bg-neutral-soft text-neutral-ink",
  info: "bg-accent-soft text-accent-hover",
  success: "bg-fit-soft text-fit-ink",
  warning: "bg-warning-soft text-warning-ink",
  danger: "bg-danger-soft text-danger",
  warm: "bg-warm-soft text-warm-ink",
  brand: "bg-accent-soft text-accent-hover",
  emerald: "bg-fit-soft text-fit-ink",
  amber: "bg-warning-soft text-warning-ink",
  slate: "bg-neutral-soft text-neutral-ink",
  red: "bg-danger-soft text-danger",
};

/** Etichetta a pillola (il `Tag` delle tavole): tipologia, ruolo, origine di un
 *  dato. 12px 500, alta almeno 24px; va a capo se il testo è lungo. Uno stato
 *  («Aperto», «Attivo») si scrive con `Status`, non con un `Badge`. */
export function Badge({ tone = "neutral", area, className, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex min-h-6 items-center gap-1 rounded-pill px-2.5 py-0.5 text-caption",
        area ? areaClassi(area).suSoft : toni[tone],
        className,
      )}
      {...props}
    />
  );
}
