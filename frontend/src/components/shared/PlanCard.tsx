import { Check, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { prezzoDisplay } from "../../lib/prezzo";
import type { Plan } from "../../types";

export function planFeatures(plan: Plan): string[] {
  const features = [
    plan.ai_check > 0 ? `${plan.ai_check} AI-check all'anno` : "AI-check non inclusi",
    plan.alert_attivo
      ? `Alert personalizzati con ${plan.alert_giorni_preavviso ?? "-"} giorni di preavviso`
      : "Alert personalizzati non inclusi",
    plan.num_account_aziendali === 1
      ? "1 account aziendale"
      : `Fino a ${plan.num_account_aziendali} account aziendali`,
  ];
  return features;
}

export function PlanCard({
  plan,
  highlighted = false,
  badge,
  footer,
  onClick,
  selected = false,
}: {
  plan: Plan;
  highlighted?: boolean;
  badge?: string;
  footer?: ReactNode;
  onClick?: () => void;
  selected?: boolean;
}) {
  const interactive = !!onClick;
  const Wrapper = interactive ? "button" : "div";
  const display = prezzoDisplay(plan.tipo_prezzo, plan.etichetta_prezzo, plan.prezzo_annuale);

  return (
    <Wrapper
      type={interactive ? "button" : undefined}
      onClick={onClick}
      aria-pressed={interactive ? selected : undefined}
      className={cn(
        "relative flex h-full flex-col rounded-xl border bg-white p-5 text-left shadow-card transition-all duration-200",
        interactive && "cursor-pointer hover:-translate-y-0.5 hover:shadow-card-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500",
        selected
          ? "border-brand-500 ring-2 ring-brand-500"
          : highlighted
            ? "border-brand-300"
            : "border-slate-200",
      )}
    >
      {badge && (
        <span className="absolute -top-2.5 left-4 inline-flex items-center gap-1 rounded-full bg-brand-500 px-2.5 py-0.5 text-xs font-semibold text-white shadow-sm">
          <Sparkles className="size-3" aria-hidden />
          {badge}
        </span>
      )}

      <h3 className="font-display text-base font-semibold text-slate-900">{plan.nome}</h3>
      {plan.descrizione && <p className="mt-1 text-xs text-slate-500">{plan.descrizione}</p>}

      <p className="mt-3">
        {/* L'etichetta «su richiesta» è testo libero: corpo ridotto per non sforare. */}
        <span
          className={cn(
            "tabular font-display font-bold text-slate-900",
            display.suRichiesta ? "text-2xl" : "text-3xl",
          )}
        >
          {display.testo}
        </span>
        {display.conSuffissoPeriodo && <span className="text-sm text-slate-500"> /anno</span>}
      </p>

      <ul className="mt-4 flex-1 space-y-2">
        {planFeatures(plan).map((feature) => (
          <li key={feature} className="flex items-start gap-2 text-sm text-slate-600">
            <Check className="mt-0.5 size-4 shrink-0 text-brand-500" aria-hidden />
            {feature}
          </li>
        ))}
      </ul>

      {footer && <div className="mt-5">{footer}</div>}
    </Wrapper>
  );
}
