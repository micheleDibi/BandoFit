import { forwardRef, useId, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface SwitchProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onChange" | "type" | "role"> {
  label: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  /** Una riga di aiuto sotto l'etichetta, in `ink-3`. */
  descrizione?: ReactNode;
  /** Classi del contenitore. */
  className?: string;
}

/** Interruttore acceso/spento con effetto immediato (avvisi email, profilo
 *  partner visibile). `button` con `role="switch"` e `aria-checked`; per una
 *  scelta che si salva con un pulsante «Salva» si usa la `Checkbox`. */
export const Switch = forwardRef<HTMLButtonElement, SwitchProps>(
  ({ label, checked, onChange, descrizione, id, className, disabled, onClick, ...props }, ref) => {
    const autoId = useId();
    const switchId = id ?? autoId;
    const descrizioneId = `${switchId}-descrizione`;
    return (
      <div className={cn("flex items-start gap-3", className)}>
        <button
          ref={ref}
          id={switchId}
          type="button"
          role="switch"
          aria-checked={checked}
          aria-describedby={descrizione ? descrizioneId : undefined}
          disabled={disabled}
          // L'`onClick` del chiamante viene prima e può fermare il cambio con `preventDefault`.
          onClick={(e) => {
            onClick?.(e);
            if (!e.defaultPrevented) onChange(!checked);
          }}
          className={cn(
            "relative mt-0.25 inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-pill transition-colors",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
            "disabled:cursor-not-allowed disabled:opacity-50",
            checked ? "bg-accent" : "bg-line-control",
          )}
          {...props}
        >
          <span
            aria-hidden
            className={cn(
              "absolute top-0.5 left-0.5 size-4 rounded-pill bg-sheet transition-transform",
              checked && "translate-x-4",
            )}
          />
        </button>
        <div className="flex min-w-0 flex-col">
          <label
            htmlFor={switchId}
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
Switch.displayName = "Switch";
