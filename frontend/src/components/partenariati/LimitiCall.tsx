import { Lock } from "lucide-react";
import { useEntitlements } from "../../hooks/useEntitlements";
import { CALL_COPY } from "../../lib/copy";
import type { PartenariatiLimite } from "../../types";
import { LinkButton } from "../ui/Button";

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

/** Pannello «il tuo piano non include le call» / «limite raggiunto», con il
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
    <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-brand-200 bg-brand-50 px-4 py-3 text-brand-900">
      <div className="flex min-w-0 items-start gap-2 text-sm">
        <Lock className="mt-0.5 size-4 shrink-0" aria-hidden />
        <div>
          <p className="font-medium">
            {nonIncluso ? CALL_COPY.pianoNonIncludeTitolo : CALL_COPY.limiteRaggiuntoTitolo}
          </p>
          <p className="mt-0.5">
            {nonIncluso ? CALL_COPY.pianoNonIncludeTesto : CALL_COPY.limiteRaggiuntoTesto}
          </p>
        </div>
      </div>
      {editable && (
        <LinkButton to="/app/abbonamento" variant="secondary" size="sm">
          {CALL_COPY.vediPiani}
        </LinkButton>
      )}
    </div>
  );
}

/** Riga di riepilogo «Call attive: 1 di 3». */
export function RiepilogoLimiteCall({ limite }: { limite: PartenariatiLimite | null }) {
  if (!limite || limite.limite === 0) return null;
  return (
    <p className="text-sm text-slate-600 tabular">
      {CALL_COPY.limiteCall(limite.usate, limite.limite)}
      <span className="block text-xs text-slate-500">
        Contano le call pubblicate di tutte le tue aziende; le bozze no.
      </span>
    </p>
  );
}
