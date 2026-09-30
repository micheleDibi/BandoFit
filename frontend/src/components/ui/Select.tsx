import { forwardRef, type SelectHTMLAttributes } from "react";
import { cn } from "../../lib/cn";
import { inputClasses } from "./Field";

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  /** Nome del controllo per lo screen reader: non c'è un'etichetta visibile
   *  («Ordina per»). Per un campo con etichetta si usa `SelectField`. */
  label: string;
}

/** `<select>` senza etichetta, per le barre degli elenchi (ordinamento, filtri
 *  a una scelta): le classi comuni dei controlli (`inputClasses`), largo quanto
 *  il contenuto (chi lo vuole a tutta larghezza passa `w-full`), freccia nativa. */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  ({ label, className, children, ...props }, ref) => (
    <select
      ref={ref}
      aria-label={label}
      className={cn(inputClasses, "w-auto cursor-pointer", className)}
      {...props}
    >
      {children}
    </select>
  ),
);
Select.displayName = "Select";
