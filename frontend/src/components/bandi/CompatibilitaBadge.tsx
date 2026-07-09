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

/** Riepilogo del pre-check: «Compatibilità 18/23» — frazione delle relazioni
 *  del bando in comune con l'azienda, colorata per banda con la stessa scala
 *  dell'AI-check. L'etichetta è esplicita: da sola la frazione non si capisce.
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
        .map(([dim, d]) => `${DIM_LABELS[dim] ?? dim} ${d.matched}/${d.totale}`)
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
        "inline-flex items-center gap-1.5 rounded-full bg-slate-50 px-2.5 py-0.5 text-xs ring-1 ring-inset ring-slate-200",
        className,
      )}
    >
      <Target className={cn("size-3 shrink-0", text)} aria-hidden />
      <span className="font-medium text-slate-500">Compatibilità</span>
      <span className={cn("tabular font-semibold", text)}>
        {matched}/{totale}
      </span>
    </span>
  );
}
