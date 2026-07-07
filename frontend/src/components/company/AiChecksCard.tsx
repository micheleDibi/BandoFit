import { Sparkles } from "lucide-react";
import { Link } from "react-router-dom";
import { useAiChecks } from "../../hooks/useAiCheck";
import { AiEsitoBadge } from "../bandi/badges";
import { formatDate } from "../../lib/format";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";

/** Storico compatto degli AI-check dell'azienda: ogni riga rimanda al bando
 *  (il report completo si riapre dalla pagina del bando). */
export function AiChecksCard() {
  const { data } = useAiChecks();
  if (!data || data.items.length === 0) return null;

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="inline-flex items-center gap-1.5 font-display text-sm font-semibold text-slate-900">
          <Sparkles className="size-4 text-brand-500" aria-hidden />
          AI-check effettuati
        </h2>
        <span className="text-xs text-slate-500">
          {data.quota.totale > 0
            ? `${data.quota.rimanenti} di ${data.quota.totale} rimanenti quest'anno`
            : "non inclusi nel piano"}
        </span>
      </div>
      <ul className="mt-3 divide-y divide-slate-100">
        {data.items.map((check) => (
          <li key={check.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5">
            <Link
              to={`/app/bandi/${check.bando_slug}`}
              className="min-w-0 flex-1 truncate text-sm font-medium text-slate-800 underline-offset-2 hover:text-brand-600 hover:underline"
              title={check.bando_titolo}
            >
              {check.bando_titolo}
            </Link>
            {check.status === "ready" && check.esito ? (
              <>
                <AiEsitoBadge esito={check.esito} />
                {check.punteggio !== null && (
                  <span className="tabular text-sm font-semibold text-slate-600">
                    {check.punteggio}/100
                  </span>
                )}
              </>
            ) : check.status === "pending" ? (
              <Badge tone="amber">In corso</Badge>
            ) : (
              <Badge tone="red">Errore</Badge>
            )}
            <span className="tabular text-xs text-slate-400">{formatDate(check.created_at)}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
