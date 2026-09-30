import { Loader2 } from "lucide-react";
import { cn } from "../../lib/cn";

export type SpinnerSize = "sm" | "md" | "lg";

export interface SpinnerProps {
  size?: SpinnerSize;
  /** Testo per le tecnologie assistive («Caricamento dei bandi»): con la label lo
   *  spinner diventa una regione `status`; senza è puramente decorativo. */
  label?: string;
  className?: string;
}

const taglie: Record<SpinnerSize, string> = {
  sm: "size-4",
  md: "size-5",
  lg: "size-8",
};

/** Indicatore di caricamento: sostituisce i `Loader2` sparsi. Per una pagina
 *  intera lo si centra dal chiamante (`flex min-h-64 items-center justify-center`). */
export function Spinner({ size = "md", label, className }: SpinnerProps) {
  return (
    <span
      role={label ? "status" : undefined}
      className={cn("inline-flex shrink-0 items-center text-ink-3", className)}
    >
      <Loader2 className={cn("animate-spin", taglie[size])} aria-hidden />
      {label && <span className="sr-only">{label}</span>}
    </span>
  );
}
