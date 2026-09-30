import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label: ReactNode;
  /** Una riga di aiuto sotto l'etichetta, in `ink-3` (collegata con `aria-describedby`). */
  descrizione?: ReactNode;
  /** Classi del contenitore (l'input non si ridimensiona). */
  className?: string;
}

/** Casella di spunta nativa (16px, `accent`) con etichetta e aiuto a destra.
 *  Le regole di gruppo («Se non scegli nulla, vale qualsiasi dimensione») si
 *  scrivono sotto il gruppo, non in ogni casella. */
export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(
  ({ label, descrizione, id, className, disabled, ...props }, ref) => {
    const autoId = useId();
    const inputId = id ?? autoId;
    const descrizioneId = `${inputId}-descrizione`;
    return (
      <div className={cn("flex items-start gap-2.5", className)}>
        <input
          ref={ref}
          id={inputId}
          type="checkbox"
          disabled={disabled}
          aria-describedby={descrizione ? descrizioneId : undefined}
          className="mt-0.75 size-4 shrink-0 cursor-pointer accent-accent disabled:cursor-not-allowed"
          {...props}
        />
        <div className="flex min-w-0 flex-col">
          <label
            htmlFor={inputId}
            className={cn("cursor-pointer text-body", disabled ? "text-ink-3" : "text-ink")}
          >
            {label}
          </label>
          {descrizione && (
            <p id={descrizioneId} className="text-small text-ink-3">
              {descrizione}
            </p>
          )}
        </div>
      </div>
    );
  },
);
Checkbox.displayName = "Checkbox";
