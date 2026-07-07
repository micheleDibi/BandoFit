import { ArrowRight, CreditCard, Users } from "lucide-react";
import { useMe } from "../../hooks/useMe";
import { formatDate } from "../../lib/format";
import { Card } from "../ui/Card";
import { LinkButton } from "../ui/Button";

/** Rimando compatto alla pagina Abbonamento (piani e add-on vivono lì). */
export function AbbonamentoTeaser() {
  const { data: me } = useMe();
  const isActiveChild = me?.family?.role === "child" && me.family.status === "active";

  return (
    <Card className="flex flex-wrap items-center justify-between gap-4 p-6">
      <div className="min-w-0">
        <h2 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
          <CreditCard className="size-4 text-brand-500" aria-hidden />
          Abbonamento
        </h2>
        <p className="mt-1 text-sm text-slate-500">
          {me?.subscription ? (
            <>
              Piano <strong className="text-slate-700">{me.subscription.plan.nome}</strong> attivo
              fino al {formatDate(me.subscription.data_scadenza)}
              {isActiveChild && (
                <span className="ml-2 inline-flex items-center gap-1 text-xs text-brand-700">
                  <Users className="size-3.5" aria-hidden />
                  ereditato dal titolare
                </span>
              )}
              .
            </>
          ) : (
            "Gestisci il tuo piano e scopri gli add-on disponibili."
          )}
        </p>
      </div>
      <LinkButton to="/app/abbonamento" variant="secondary" size="sm">
        Gestisci abbonamento
        <ArrowRight className="size-3.5" aria-hidden />
      </LinkButton>
    </Card>
  );
}
