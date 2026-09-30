import { cn } from "../../lib/cn";

export type ProgressBarTono = "accent" | "fit";

export interface ProgressBarProps {
  valore: number;
  massimo: number;
  /** Nome della barra per le tecnologie assistive («AI-check usati»). */
  label: string;
  /** `fit` per la compatibilità e gli esiti positivi; `accent` per il resto. */
  tono?: ProgressBarTono;
  className?: string;
}

const riempimenti: Record<ProgressBarTono, string> = {
  accent: "bg-accent",
  fit: "bg-fit",
};

/** Barra di avanzamento su `sunken`, 6px, angoli a pillola. È l'unico posto in cui
 *  la larghezza si scrive con `style`: le pagine passano i numeri, non le classi. */
export function ProgressBar({ valore, massimo, label, tono = "accent", className }: ProgressBarProps) {
  const max = massimo > 0 ? massimo : 1;
  const attuale = Math.min(Math.max(valore, 0), max);
  const percento = (attuale / max) * 100;
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={attuale}
      className={cn("h-1.5 w-full overflow-hidden rounded-pill bg-sunken", className)}
    >
      <div className={cn("h-full rounded-pill", riempimenti[tono])} style={{ width: `${percento}%` }} />
    </div>
  );
}
