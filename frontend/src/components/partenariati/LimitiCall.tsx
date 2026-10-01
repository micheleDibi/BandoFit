import { useEntitlements } from "../../hooks/useEntitlements";
import { CALL_COPY } from "../../lib/copy";
import type { PartenariatiLimite } from "../../types";
import { Alert } from "../ui/Alert";
import { ProgressRing } from "../ui/ProgressRing";
import { TextLink } from "../ui/TextLink";

/** Limite delle call attive dal piano (`/me/entitlements.partenariati`), con
 *  il ripiego sui limiti riportati dalla call. `limite` null = illimitate,
 *  0 = non incluse nel piano. null = dato non disponibile (lo applica comunque
 *  il server). */
export function useLimiteCall(ripiego?: PartenariatiLimite | null): PartenariatiLimite | null {
  const { data } = useEntitlements();
  return data?.partenariati?.call_attive ?? ripiego ?? null;
}

export type StatoLimite = "sconosciuto" | "non_incluso" | "esaurito" | "disponibile";

export function statoLimite(limite: PartenariatiLimite | null): StatoLimite {
  if (!limite) return "sconosciuto";
  if (limite.limite === 0) return "non_incluso";
  if (limite.residuo !== null && limite.residuo <= 0) return "esaurito";
  return "disponibile";
}

/** Avviso «il tuo piano non include le call» / «limite raggiunto», con il
 *  rimando ai piani (solo per il titolare: i membri non cambiano piano). */
export function AvvisoLimiteCall({
  limite,
  editable,
}: {
  limite: PartenariatiLimite | null;
  editable: boolean;
}) {
  const stato = statoLimite(limite);
  if (stato !== "non_incluso" && stato !== "esaurito") return null;
  const nonIncluso = stato === "non_incluso";
  return (
    // Stato del piano, non un errore: annuncio educato (`status`), non `alert`.
    <Alert
      tono="attenzione"
      ruolo="status"
      titolo={nonIncluso ? CALL_COPY.pianoNonIncludeTitolo : CALL_COPY.limiteRaggiuntoTitolo}
      azione={editable ? <TextLink to="/app/abbonamento">{CALL_COPY.vediPiani}</TextLink> : undefined}
    >
      {nonIncluso ? CALL_COPY.pianoNonIncludeTesto : CALL_COPY.limiteRaggiuntoTesto}
    </Alert>
  );
}

/** Riga di riepilogo «Call attive: 1 di 3», con l'anello dell'uso quando il
 *  piano ha un limite (il numero sta scritto anche in parole accanto). */
export function RiepilogoLimiteCall({ limite }: { limite: PartenariatiLimite | null }) {
  if (!limite || limite.limite === 0) return null;
  const massimo = limite.limite;
  return (
    <div className="flex items-center gap-3">
      {massimo !== null && (
        // Decorativo: la frase accanto dice già «N di M».
        <span aria-hidden className="flex shrink-0">
          <ProgressRing
            value={limite.usate}
            max={massimo}
            size={44}
            tono={limite.residuo !== null && limite.residuo <= 0 ? "warm" : "partenariati"}
            label={CALL_COPY.limiteCall(limite.usate, massimo)}
          >
            {limite.usate}
          </ProgressRing>
        </span>
      )}
      <p className="text-small text-ink-2 tabular-nums">
        {CALL_COPY.limiteCall(limite.usate, limite.limite)}
        <span className="block text-ink-3">
          Contano le call pubblicate di tutte le tue aziende; le bozze no.
        </span>
      </p>
    </div>
  );
}
