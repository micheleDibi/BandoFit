import { Check } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { prezzoDisplay } from "../../lib/prezzo";
import type { Plan } from "../../types";
import { Badge } from "../ui/Badge";

function alertFeature(plan: Plan): string {
  // Avvisi nuovi-bandi: copy onesto guidato dal ritardo del piano.
  if (plan.alert_attivo && plan.alert_ritardo_giorni != null) {
    if (plan.alert_ritardo_giorni === 0)
      return "Nuovi bandi compatibili via email il giorno stesso della pubblicazione";
    if (plan.alert_ritardo_giorni === 1)
      return "Nuovi bandi compatibili via email il giorno dopo la pubblicazione";
    return `Nuovi bandi compatibili via email dopo ${plan.alert_ritardo_giorni} giorni dalla pubblicazione`;
  }
  return "Avvisi email sui nuovi bandi non inclusi";
}

export function planFeatures(plan: Plan): string[] {
  // Override esplicito (es. piano «tailored»): sostituisce i punti standard.
  if (plan.features_override && plan.features_override.length > 0)
    return plan.features_override;
  const features = [
    plan.ai_check > 0 ? `${plan.ai_check} AI-check all'anno` : "AI-check non inclusi",
    alertFeature(plan),
    plan.num_account_aziendali === 1
      ? "1 account aziendale"
      : `Fino a ${plan.num_account_aziendali} account aziendali`,
  ];
  return features;
}

/** Card di un piano: foglio bianco con ombra `card`. Il piano in evidenza
 *  (`highlighted`) ha il bordo `accent`, l'ombra più ampia e l'etichetta
 *  (`badge`) a pillola piena sul bordo in alto; selezionato, l'anello `accent`
 *  pieno. Con `onClick` la card intera è un pulsante e risponde al passaggio. */
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
        "relative flex h-full flex-col rounded-panel border bg-sheet p-5 text-left shadow-card",
        interactive &&
          "cursor-pointer transition duration-150 ease-uscita hover:shadow-card-hover motion-safe:hover:-translate-y-0.5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        selected
          ? "border-accent ring-2 ring-accent"
          : highlighted
            ? "border-accent shadow-card-hover ring-1 ring-accent"
            : "border-line",
        badge && "pt-7",
      )}
    >
      <h3 className="text-title-section text-ink">{plan.nome}</h3>
      {/* L'etichetta sul bordo in alto: pillola piena `accent` (testo bianco, 6,4:1).
          Dopo il nome nel DOM, così si legge «Pro, Consigliato». */}
      {badge && (
        <Badge
          tone="info"
          className="absolute -top-3 left-5 bg-accent px-3 font-semibold text-on-accent shadow-card"
        >
          {badge}
        </Badge>
      )}
      {plan.descrizione && <p className="mt-1 text-small text-ink-3">{plan.descrizione}</p>}

      <p className="mt-4">
        {/* L'etichetta «su richiesta» è testo libero: corpo ridotto per non sforare. */}
        <span className={cn("text-ink", display.suRichiesta ? "text-figure-sm" : "text-figure")}>
          {display.testo}
        </span>
        {display.conSuffissoPeriodo && <span className="text-small text-ink-3"> /anno</span>}
      </p>

      <ul className="mt-4 flex flex-1 flex-col gap-2 border-t border-line pt-4">
        {planFeatures(plan).map((feature) => (
          <li key={feature} className="flex items-start gap-2 text-body text-ink-2">
            <Check className="mt-0.75 size-4 shrink-0 text-accent" aria-hidden />
            {feature}
          </li>
        ))}
      </ul>

      {footer && <div className="mt-5">{footer}</div>}
    </Wrapper>
  );
}
