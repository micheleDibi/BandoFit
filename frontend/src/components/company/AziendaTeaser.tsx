import { ArrowRight, Building2 } from "lucide-react";
import { useCompany } from "../../hooks/useCompany";
import { Card } from "../ui/Card";
import { LinkButton } from "../ui/Button";

/** Rimando compatto a «Dati azienda»: dati, dossier certificato e
 *  documenti ufficiali vivono tutti lì. */
export function AziendaTeaser() {
  const { data } = useCompany();
  const ragione = data?.company?.ragione_sociale;

  return (
    <Card className="flex flex-wrap items-center justify-between gap-4 p-6">
      <div className="min-w-0">
        <h2 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
          <Building2 className="size-4 text-brand-500" aria-hidden />
          Dati aziendali
        </h2>
        <p className="mt-1 text-sm text-slate-500">
          {ragione
            ? `${ragione} — dati, dossier certificato e documenti ufficiali si gestiscono in «Dati azienda».`
            : "Compila i dati della tua azienda in «Dati azienda»: alimentano l'AI-check e «Bandi per te»."}
        </p>
      </div>
      <LinkButton to="/app/azienda" variant="secondary" size="sm">
        Vai ai dati azienda
        <ArrowRight className="size-3.5" aria-hidden />
      </LinkButton>
    </Card>
  );
}
