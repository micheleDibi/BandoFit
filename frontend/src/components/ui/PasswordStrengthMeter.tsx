import { useEffect, useState } from "react";
import { cn } from "../../lib/cn";
import { loadZxcvbn, strengthFromScore, type Strength } from "../../lib/passwordStrength";

type CheckFn = NonNullable<Awaited<ReturnType<typeof loadZxcvbn>>>;

/** Colori dei tre livelli con i token del design system, per numero di
 *  segmenti: `Strength` dice solo livello ed etichetta, l'aspetto sta qui. */
const livelli: Record<Strength["segments"], { barra: string; testo: string }> = {
  1: { barra: "bg-danger", testo: "text-danger" },
  2: { barra: "bg-warning-line", testo: "text-warning-ink" },
  3: { barra: "bg-fit", testo: "text-fit-ink" },
};

/** Indicatore di robustezza password: informativo (non blocca mai il submit).
 *  Barra a 3 segmenti + etichetta testuale in un `role="status"` (aria-live
 *  implicito): il livello non è mai comunicato dal solo colore. L'engine
 *  zxcvbn si scarica al primo carattere digitato; finché non è pronto la
 *  barra resta neutra con l'altezza già riservata (niente layout shift).
 *  Va sotto un `PasswordField` con `autoComplete="new-password"`. */
export function PasswordStrengthMeter({
  password,
  userInputs,
  className,
}: {
  password: string;
  /** Dati dell'utente (email, nome…) per penalizzare le password derivate. */
  userInputs?: string[];
  className?: string;
}) {
  const [check, setCheck] = useState<CheckFn | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!password || check || failed) return;
    let attivo = true;
    loadZxcvbn().then((fn) => {
      if (!attivo) return;
      if (fn) setCheck(() => fn);
      else setFailed(true);
    });
    return () => {
      attivo = false;
    };
  }, [password, check, failed]);

  if (!password || failed) return null;

  const strength: Strength | null = check
    ? strengthFromScore(check(password, userInputs).score)
    : null;
  const livello = strength ? livelli[strength.segments] : null;

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <div className="flex gap-1" aria-hidden>
        {[1, 2, 3].map((segment) => (
          <span
            key={segment}
            className={cn(
              "h-1 flex-1 rounded-pill transition-colors",
              strength && livello && segment <= strength.segments ? livello.barra : "bg-sunken",
            )}
          />
        ))}
      </div>
      <p role="status" className="text-small text-ink-3">
        {strength && livello ? (
          <>
            Robustezza password:{" "}
            <span className={cn("font-medium", livello.testo)}>{strength.label}</span>
          </>
        ) : (
          // Spazio non separabile: uno spazio normale collassa e la riga perde l'altezza.
          " "
        )}
      </p>
    </div>
  );
}
