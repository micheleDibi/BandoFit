import { Target } from "lucide-react";
import { cn } from "../../lib/cn";
import { scoreColorClasses } from "../../lib/scoreColor";
import type { Compatibilita } from "../../types";

const DIM_LABELS: Record<string, string> = {
  regioni: "Regioni",
  ateco: "ATECO",
  settori: "Settori",
  beneficiari: "Beneficiari",
};

/** Punteggio di compatibilità a-priori azienda↔bando: frazione «in comune /
 *  totale» delle relazioni del bando (es. «18/23»), colorata per banda con la
 *  stessa scala dell'AI-check. Il dettaglio per dimensione è nel tooltip.
 *  Da renderizzare solo quando `compatibilita` è presente (profilo sufficiente). */
export function CompatibilitaBadge({
  compatibilita,
  className,
}: {
  compatibilita: Compatibilita;
  className?: string;
}) {
  const { punteggio, matched, totale, dimensioni } = compatibilita;
  const { text } = scoreColorClasses(punteggio);

  const dettaglio = dimensioni
    ? Object.entries(dimensioni)
        .map(([dim, { matched: m, totale: t }]) => `${DIM_LABELS[dim] ?? dim} ${m}/${t}`)
        .join(" · ")
    : "";
  const label = `Compatibilità ${punteggio}%: ${matched}/${totale} elementi del bando in comune con la tua azienda${
    dettaglio ? ` (${dettaglio})` : ""
  }`;

  return (
    <span
      title={label}
      aria-label={label}
      className={cn(
        "inline-flex items-center gap-1 rounded-full bg-slate-50 px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset ring-slate-200",
        className,
      )}
    >
      <Target className={cn("size-3", text)} aria-hidden />
      <span className={text}>
        {matched}/{totale}
      </span>
    </span>
  );
}
