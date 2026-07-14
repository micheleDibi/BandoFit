import { useEffect, useState } from "react";
import { cn } from "../../lib/cn";
import { loadZxcvbn, strengthFromScore, type Strength } from "../../lib/passwordStrength";

type CheckFn = NonNullable<Awaited<ReturnType<typeof loadZxcvbn>>>;

/** Indicatore di robustezza password: informativo (non blocca mai il submit).
 *  Barra a 3 segmenti + etichetta testuale in un `role="status"` (aria-live
 *  implicito): il livello non è mai comunicato dal solo colore. L'engine
 *  zxcvbn si scarica al primo carattere digitato; finché non è pronto la
 *  barra resta neutra con l'altezza già riservata (niente layout shift). */
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

  return (
    <div className={cn("space-y-1", className)}>
      <div className="flex gap-1" aria-hidden>
        {[1, 2, 3].map((segment) => (
          <span
            key={segment}
            className={cn(
              "h-1 flex-1 rounded-full transition-colors",
              strength && segment <= strength.segments ? strength.barClass : "bg-slate-200",
            )}
          />
        ))}
      </div>
      <p role="status" className="text-xs text-slate-500">
        {strength ? (
          <>
            Robustezza password:{" "}
            <span className={cn("font-medium", strength.textClass)}>{strength.label}</span>
          </>
        ) : (
          " "
        )}
      </p>
    </div>
  );
}
