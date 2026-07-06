import { ArrowRight, Sparkles } from "lucide-react";
import { usePreferences } from "../../hooks/usePreferences";
import { Card } from "../ui/Card";
import { LinkButton } from "../ui/Button";

/** Rimando compatto alla pagina Preferenze (l'editor completo vive lì). */
export function PreferenzeTeaser() {
  const { data } = usePreferences();
  const count = data
    ? Object.values(data).reduce((acc, ids) => acc + ids.length, 0)
    : 0;

  return (
    <Card className="flex flex-wrap items-center justify-between gap-4 p-6">
      <div className="min-w-0">
        <h2 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
          <Sparkles className="size-4 text-brand-500" aria-hidden />
          Preferenze bandi
        </h2>
        <p className="mt-1 text-sm text-slate-500">
          {count > 0
            ? `Stai seguendo ${count} ${count === 1 ? "valore" : "valori"} oltre al profilo della tua azienda.`
            : "Segui altri ATECO, regioni o programmi oltre a quelli della tua azienda: alimentano «Bandi per te»."}
        </p>
      </div>
      <LinkButton to="/app/preferenze" variant="secondary" size="sm">
        Gestisci preferenze
        <ArrowRight className="size-3.5" aria-hidden />
      </LinkButton>
    </Card>
  );
}
