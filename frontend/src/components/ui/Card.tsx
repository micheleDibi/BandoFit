import type { HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

/** Riquadro con bordo, solo per oggetti uguali da confrontare (mai un riquadro
 *  dentro un riquadro): bordo `line`, raggio `panel`, senza ombra, padding 20.
 *  Un `p-*` passato in `className` vince sul default. */
export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-panel border border-line bg-sheet p-5", className)}
      {...props}
    />
  );
}
