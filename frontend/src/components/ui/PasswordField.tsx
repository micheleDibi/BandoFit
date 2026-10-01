import { forwardRef, useId, useState, type InputHTMLAttributes } from "react";
import { cn } from "../../lib/cn";
import { inputClasses } from "./Field";
import { InlineError } from "./InlineError";

export interface PasswordFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label: string;
  error?: string;
  helper?: string;
}

/** Campo password con «Mostra / Nascondi» dentro il campo: una sola versione
 *  per accesso, registrazione, invito e reimpostazione (prima erano quattro
 *  copie). Struttura e stile del `TextField`; `autoComplete` lo passa chi lo
 *  usa («current-password» o «new-password»). */
export const PasswordField = forwardRef<HTMLInputElement, PasswordFieldProps>(
  ({ label, error, helper, required, id, className, ...props }, ref) => {
    const [visibile, setVisibile] = useState(false);
    const autoId = useId();
    const fieldId = id ?? autoId;
    const erroreId = `${fieldId}-errore`;
    const aiutoId = `${fieldId}-aiuto`;
    return (
      <div className="flex flex-col gap-1.5">
        <label htmlFor={fieldId} className="text-small font-medium text-ink">
          {label}
          {required && (
            <span className="text-danger" aria-hidden>
              {" "}
              *
            </span>
          )}
        </label>
        <div className="relative">
          <input
            ref={ref}
            id={fieldId}
            type={visibile ? "text" : "password"}
            required={required}
            aria-invalid={!!error}
            aria-describedby={error ? erroreId : helper ? aiutoId : undefined}
            className={cn(
              inputClasses,
              "pr-24",
              error &&
                "border-danger enabled:hover:border-danger focus:border-danger focus-visible:outline-danger focus-visible:ring-danger/15",
              className,
            )}
            {...props}
          />
          <button
            type="button"
            onClick={() => setVisibile((v) => !v)}
            aria-label={visibile ? "Nascondi password" : "Mostra password"}
            aria-controls={fieldId}
            className={cn(
              "absolute inset-y-1 right-1 inline-flex cursor-pointer items-center rounded-control px-2",
              "text-small font-medium text-accent-hover hover:bg-accent-soft",
              "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
            )}
          >
            {visibile ? "Nascondi" : "Mostra"}
          </button>
        </div>
        {error ? (
          <InlineError id={erroreId}>{error}</InlineError>
        ) : helper ? (
          <p id={aiutoId} className="text-small text-ink-3">
            {helper}
          </p>
        ) : null}
      </div>
    );
  },
);
PasswordField.displayName = "PasswordField";
