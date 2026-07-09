import { ArrowUpRight, Gauge, Loader2, Sparkles } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";
import { QuotaUpgradeBanner } from "../components/aicheck/QuotaUpgradeBanner";
import { AiEsitoBadge } from "../components/bandi/badges";
import { Badge } from "../components/ui/Badge";
import { buttonClasses } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { useAiChecks } from "../hooks/useAiCheck";
import { useMe } from "../hooks/useMe";
import { usePlans } from "../hooks/usePlans";
import { apiErrorMessage } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { scoreColorClasses } from "../lib/scoreColor";
import type { AiCheck } from "../types";

/** Un gruppo per bando: l'analisi più recente in evidenza + numero versioni. */
interface CheckGroup {
  slug: string;
  latest: AiCheck;
  count: number;
}

function groupBySlug(items: AiCheck[]): CheckGroup[] {
  const groups = new Map<string, CheckGroup>();
  for (const check of items) {
    const existing = groups.get(check.bando_slug);
    if (existing) {
      existing.count += 1; // items arrivano già dal più recente
    } else {
      groups.set(check.bando_slug, { slug: check.bando_slug, latest: check, count: 1 });
    }
  }
  return [...groups.values()];
}

function StatTile({
  icon: Icon,
  label,
  children,
}: {
  icon: typeof Gauge;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="p-5">
      <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-slate-400">
        <Icon className="size-3.5" aria-hidden />
        {label}
      </div>
      <div className="mt-2">{children}</div>
    </Card>
  );
}

function ScoreBar({ punteggio }: { punteggio: number }) {
  const colori = scoreColorClasses(punteggio);
  return (
    <div className="flex w-28 items-center gap-2">
      <div className="h-1.5 flex-1 rounded-full bg-slate-100">
        <div className={`h-1.5 rounded-full ${colori.bar}`} style={{ width: `${punteggio}%` }} />
      </div>
      <span className={`tabular shrink-0 font-display text-sm font-bold ${colori.text}`}>
        {punteggio}
        <span className="text-[10px] font-medium text-slate-400">/100</span>
      </span>
    </div>
  );
}

function GroupRow({ group }: { group: CheckGroup }) {
  const { latest } = group;
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-4">
      <div className="min-w-0 flex-1 basis-64">
        <Link
          to={`/app/bandi/${group.slug}`}
          className="line-clamp-2 font-medium text-slate-800 underline-offset-2 hover:text-brand-600 hover:underline"
          title={latest.bando_titolo}
        >
          {latest.bando_titolo}
        </Link>
        <p className="mt-0.5 text-xs text-slate-400">
          {formatDateTime(latest.ready_at ?? latest.created_at)}
          {group.count > 1 && ` · ${group.count} analisi`}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {latest.status === "pending" ? (
          <span className="inline-flex items-center gap-1.5 text-sm font-medium text-amber-700">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            Analisi in corso…
          </span>
        ) : latest.status === "error" ? (
          <Badge tone="red">Non riuscita</Badge>
        ) : (
          <>
            {latest.esito && <AiEsitoBadge esito={latest.esito} />}
            {latest.punteggio !== null && <ScoreBar punteggio={latest.punteggio} />}
          </>
        )}
        <Link
          to={`/app/bandi/${group.slug}#ai-check-report`}
          className={buttonClasses("secondary", "sm")}
        >
          Apri report
          <ArrowUpRight className="size-3.5" aria-hidden />
        </Link>
      </div>
    </li>
  );
}

export default function AiCheck() {
  const { data, isPending, isError, error, refetch } = useAiChecks();
  // Servono al banner per capire se un upgrade è davvero possibile: entrambe
  // sono già in cache TanStack (navbar e pagina Abbonamento).
  const { data: me } = useMe();
  const { data: plans } = usePlans();

  const groups = useMemo(() => groupBySlug(data?.items ?? []), [data]);
  const quota = data?.quota;
  const quotaPct =
    quota && quota.totale > 0 ? Math.min(100, Math.round((quota.usati / quota.totale) * 100)) : 0;

  return (
    <div className="mx-auto max-w-5xl">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="inline-flex items-center gap-2 font-display text-2xl font-bold tracking-tight text-slate-900">
            <Sparkles className="size-6 text-brand-500" aria-hidden />
            AI-check
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Le analisi di compatibilità tra la tua azienda e i bandi: l'AI verifica ogni
            requisito citando il testo del bando e i tuoi dati.
          </p>
        </div>
      </div>

      {isPending ? (
        <div className="mt-6 space-y-4">
          <Skeleton className="h-24 w-full sm:max-w-sm" />
          <Skeleton className="h-48 w-full" />
        </div>
      ) : isError ? (
        <div className="mt-6">
          <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
        </div>
      ) : (
        <>
          {/* Quota del piano */}
          <div className="mt-6 sm:max-w-sm">
            <StatTile icon={Gauge} label="Disponibili quest'anno">
              {quota && quota.totale > 0 ? (
                <>
                  <p className="tabular font-display text-2xl font-bold text-slate-900">
                    {quota.rimanenti}
                    <span className="text-sm font-medium text-slate-400"> di {quota.totale}</span>
                  </p>
                  <div className="mt-2 h-1.5 rounded-full bg-slate-100">
                    <div
                      className="h-1.5 rounded-full bg-brand-500"
                      style={{ width: `${100 - quotaPct}%` }}
                    />
                  </div>
                </>
              ) : (
                <p className="text-sm text-slate-500">
                  Non inclusi nel tuo piano.{" "}
                  <Link to="/app/abbonamento" className="font-medium text-brand-600 hover:underline">
                    Vedi i piani
                  </Link>
                </p>
              )}
            </StatTile>
          </div>

          <QuotaUpgradeBanner quota={quota} me={me} plans={plans} />

          {/* Elenco */}
          {groups.length === 0 ? (
            <div className="mt-6">
              <EmptyState
                title="Nessun AI-check ancora"
                description="Apri un bando che ti interessa e avvia la verifica di compatibilità: troverai qui tutti i report."
                action={
                  <Link to="/app/bandi" className={buttonClasses("primary", "md")}>
                    Esplora i bandi
                  </Link>
                }
              />
            </div>
          ) : (
            <Card className="mt-6 overflow-hidden p-0">
              <ul className="divide-y divide-slate-100">
                {groups.map((group) => (
                  <GroupRow key={group.slug} group={group} />
                ))}
              </ul>
            </Card>
          )}

          <p className="mt-4 text-xs text-slate-400">
            Report generati con l'AI a scopo orientativo: verifica sempre il testo ufficiale
            del bando prima di candidarti. Ogni nuova analisi consuma 1 AI-check del piano.
          </p>
        </>
      )}
    </div>
  );
}
