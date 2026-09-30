import { CircleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface InlineErrorProps {
  children: ReactNode;
  /** Id da referenziare con `aria-describedby` dal campo che ha causato l'errore. */
  id?: string;
  className?: string;
}

/** Errore in linea sotto un campo: icona e testo 13px in `danger`, `role="alert"`
 *  (annunciato appena compare). Che cosa è successo e come rimediare, senza scuse. */
export function InlineError({ children, id, className }: InlineErrorProps) {
  return (
    <p
      id={id}
      role="alert"
      className={cn("flex items-start gap-2 text-small font-medium text-danger", className)}
    >
      <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
      <span>{children}</span>
    </p>
  );
}
