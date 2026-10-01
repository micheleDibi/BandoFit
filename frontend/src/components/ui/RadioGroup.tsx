import { useId, type ReactNode } from "react";
import { cn } from "../../lib/cn";
import { InlineError } from "./InlineError";

export interface RadioOpzione {
  id: string;
  label: ReactNode;
  /** Una riga di aiuto sotto l'etichetta («Guida il progetto e tiene i rapporti con l'ente»). */
  descrizione?: ReactNode;
  disabled?: boolean;
}

export interface RadioGroupProps {
  /** Attributo `name` condiviso dalle opzioni. */
  nome: string;
  opzioni: readonly RadioOpzione[];
  /** `id` dell'opzione scelta; `null` se nessuna. */
  valore: string | null;
  onChange: (id: string) => void;
  /** Titolo del gruppo (la `legend`). */
  legend: string;
  /** Spiegazione del gruppo sotto la `legend`, in `ink-3`, collegata al
   *  `fieldset` con `aria-describedby` (insieme all'eventuale errore). */
  descrizione?: ReactNode;
  /** Errore sotto il gruppo, con `InlineError`. */
  error?: string;
  /** Disattiva tutte le opzioni. */
  disabled?: boolean;
  className?: string;
}

/** Gruppo di scelte esclusive: `fieldset` con `legend` e pulsanti radio nativi
 *  (16px, `accent`), uno per riga con etichetta e aiuto come la `Checkbox`. */
export function RadioGroup({
  nome,
  opzioni,
  valore,
  onChange,
  legend,
  descrizione,
  error,
  disabled,
  className,
}: RadioGroupProps) {
  const base = useId();
  const erroreId = `${base}-errore`;
  const aiutoId = `${base}-aiuto`;
  const descrittoDa =
    [descrizione ? aiutoId : undefined, error ? erroreId : undefined].filter(Boolean).join(" ") ||
    undefined;
  return (
    <fieldset className={cn("flex flex-col gap-2", className)} aria-describedby={descrittoDa}>
      <legend className={cn("text-small font-medium text-ink", descrizione ? "mb-1" : "mb-2")}>
        {legend}
      </legend>
      {descrizione && (
        <p id={aiutoId} className="text-small text-ink-3">
          {descrizione}
        </p>
      )}
      {opzioni.map((opzione) => {
        const id = `${base}-${opzione.id}`;
        const descrizioneId = `${id}-descrizione`;
        const spenta = disabled || opzione.disabled;
        return (
          <div key={opzione.id} className="flex items-start gap-2.5">
            <input
              id={id}
              type="radio"
              name={nome}
              value={opzione.id}
              checked={valore === opzione.id}
              onChange={() => onChange(opzione.id)}
              disabled={spenta}
              aria-describedby={opzione.descrizione ? descrizioneId : undefined}
              className="mt-0.75 size-4 shrink-0 cursor-pointer accent-accent disabled:cursor-not-allowed"
            />
            <div className="flex min-w-0 flex-col">
              <label
                htmlFor={id}
                className={cn("cursor-pointer text-body", spenta ? "text-ink-3" : "text-ink")}
              >
                {opzione.label}
              </label>
              {opzione.descrizione && (
                <p id={descrizioneId} className="text-small text-ink-3">
                  {opzione.descrizione}
                </p>
              )}
            </div>
          </div>
        );
      })}
      {error && <InlineError id={erroreId}>{error}</InlineError>}
    </fieldset>
  );
}
