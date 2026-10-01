import {
  forwardRef,
  useId,
  type InputHTMLAttributes,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { cn } from "../../lib/cn";
import { InlineError } from "./InlineError";

/** Classi comuni dei controlli: bordo `line-control`, altezza 40, raggio
 *  `control`; a fuoco il bordo diventa `accent` (l'anello lo dà la regola
 *  globale, qui senza scarto per non staccarsi dal bordo). */
export const inputClasses =
  "h-10 w-full rounded-control border border-line-control bg-sheet px-3 text-body text-ink " +
  "placeholder:text-ink-3 transition-colors duration-150 " +
  "focus:border-accent focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-accent " +
  "disabled:cursor-not-allowed disabled:bg-sunken disabled:text-ink-3";

const errorClasses = "border-danger focus:border-danger focus-visible:outline-danger";

/** `aria-describedby` del chiamante (un aiuto esterno) unito all'id dell'errore. */
function descrittoDa(esterno: string | undefined, errorId: string, error?: string) {
  return [esterno, error ? errorId : undefined].filter(Boolean).join(" ") || undefined;
}

interface FieldWrapperProps {
  label: string;
  required?: boolean;
  error?: string;
  helper?: string;
  htmlFor: string;
  errorId: string;
  children: React.ReactNode;
}

function FieldWrapper({
  label,
  required,
  error,
  helper,
  htmlFor,
  errorId,
  children,
}: FieldWrapperProps) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={htmlFor} className="block text-small font-medium text-ink">
        {label}
        {required && (
          <span className="text-danger" aria-hidden>
            {" "}
            *
          </span>
        )}
      </label>
      {children}
      {error ? (
        <InlineError id={errorId}>{error}</InlineError>
      ) : helper ? (
        <p className="text-small text-ink-3">{helper}</p>
      ) : null}
    </div>
  );
}

export interface TextFieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  error?: string;
  helper?: string;
}

export const TextField = forwardRef<HTMLInputElement, TextFieldProps>(
  ({ label, error, helper, required, id, className, "aria-describedby": describedBy, ...props }, ref) => {
    const autoId = useId();
    const fieldId = id ?? autoId;
    const errorId = `${fieldId}-errore`;
    return (
      <FieldWrapper
        label={label}
        required={required}
        error={error}
        helper={helper}
        htmlFor={fieldId}
        errorId={errorId}
      >
        <input
          ref={ref}
          id={fieldId}
          required={required}
          aria-invalid={!!error}
          className={cn(inputClasses, error && errorClasses, className)}
          {...props}
          aria-describedby={descrittoDa(describedBy, errorId, error)}
        />
      </FieldWrapper>
    );
  },
);
TextField.displayName = "TextField";

export interface TextareaFieldProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label: string;
  error?: string;
  helper?: string;
}

export const TextareaField = forwardRef<HTMLTextAreaElement, TextareaFieldProps>(
  ({ label, error, helper, required, id, className, "aria-describedby": describedBy, ...props }, ref) => {
    const autoId = useId();
    const fieldId = id ?? autoId;
    const errorId = `${fieldId}-errore`;
    return (
      <FieldWrapper
        label={label}
        required={required}
        error={error}
        helper={helper}
        htmlFor={fieldId}
        errorId={errorId}
      >
        <textarea
          ref={ref}
          id={fieldId}
          required={required}
          aria-invalid={!!error}
          className={cn(
            inputClasses,
            "h-auto min-h-20 resize-y py-2",
            error && errorClasses,
            className,
          )}
          {...props}
          aria-describedby={descrittoDa(describedBy, errorId, error)}
        />
      </FieldWrapper>
    );
  },
);
TextareaField.displayName = "TextareaField";

export interface SelectFieldProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label: string;
  error?: string;
  helper?: string;
}

export const SelectField = forwardRef<HTMLSelectElement, SelectFieldProps>(
  (
    { label, error, helper, required, id, className, children, "aria-describedby": describedBy, ...props },
    ref,
  ) => {
    const autoId = useId();
    const fieldId = id ?? autoId;
    const errorId = `${fieldId}-errore`;
    return (
      <FieldWrapper
        label={label}
        required={required}
        error={error}
        helper={helper}
        htmlFor={fieldId}
        errorId={errorId}
      >
        <select
          ref={ref}
          id={fieldId}
          required={required}
          aria-invalid={!!error}
          className={cn(inputClasses, "cursor-pointer", error && errorClasses, className)}
          {...props}
          aria-describedby={descrittoDa(describedBy, errorId, error)}
        >
          {children}
        </select>
      </FieldWrapper>
    );
  },
);
SelectField.displayName = "SelectField";
