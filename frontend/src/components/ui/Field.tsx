import { forwardRef, useId, type InputHTMLAttributes, type SelectHTMLAttributes } from "react";
import { cn } from "../../lib/cn";

const inputClasses =
  "h-10 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-900 " +
  "placeholder:text-slate-400 transition-colors duration-150 " +
  "focus:border-brand-500 focus:outline-2 focus:outline-offset-0 focus:outline-brand-500/30 " +
  "disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500";

interface FieldWrapperProps {
  label: string;
  required?: boolean;
  error?: string;
  helper?: string;
  htmlFor: string;
  children: React.ReactNode;
}

function FieldWrapper({ label, required, error, helper, htmlFor, children }: FieldWrapperProps) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-sm font-medium text-slate-700">
        {label}
        {required && (
          <span className="text-red-500" aria-hidden>
            {" "}
            *
          </span>
        )}
      </label>
      {children}
      {error ? (
        <p className="text-sm text-red-600" role="alert">
          {error}
        </p>
      ) : helper ? (
        <p className="text-sm text-slate-500">{helper}</p>
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
  ({ label, error, helper, required, id, className, ...props }, ref) => {
    const autoId = useId();
    const fieldId = id ?? autoId;
    return (
      <FieldWrapper label={label} required={required} error={error} helper={helper} htmlFor={fieldId}>
        <input
          ref={ref}
          id={fieldId}
          required={required}
          aria-invalid={!!error}
          className={cn(inputClasses, error && "border-red-400 focus:border-red-500", className)}
          {...props}
        />
      </FieldWrapper>
    );
  },
);
TextField.displayName = "TextField";

export interface SelectFieldProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label: string;
  error?: string;
  helper?: string;
}

export const SelectField = forwardRef<HTMLSelectElement, SelectFieldProps>(
  ({ label, error, helper, required, id, className, children, ...props }, ref) => {
    const autoId = useId();
    const fieldId = id ?? autoId;
    return (
      <FieldWrapper label={label} required={required} error={error} helper={helper} htmlFor={fieldId}>
        <select
          ref={ref}
          id={fieldId}
          required={required}
          aria-invalid={!!error}
          className={cn(inputClasses, "cursor-pointer", error && "border-red-400", className)}
          {...props}
        >
          {children}
        </select>
      </FieldWrapper>
    );
  },
);
SelectField.displayName = "SelectField";
