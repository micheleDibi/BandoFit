import { useMemo } from "react";
import { Link } from "react-router-dom";
import { QuotaUpgradeBanner } from "../components/aicheck/QuotaUpgradeBanner";
import { AiEsitoBadge } from "../components/bandi/badges";
import { LinkButton } from "../components/ui/Button";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { ProgressBar } from "../components/ui/ProgressBar";
import { Section, SectionHeader } from "../components/ui/SectionHeader";
import { Spinner } from "../components/ui/Spinner";
import { Status } from "../components/ui/Status";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { TextLink } from "../components/ui/TextLink";
import { useAiChecks } from "../hooks/useAiCheck";
import { useMe } from "../hooks/useMe";
import { usePlans } from "../hooks/usePlans";
import { apiErrorMessage } from "../lib/api";
import { formatDateTime } from "../lib/format";
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

/** Riga dello storico: il bando, la data dell'ultima analisi, lo stato o
 *  l'esito con il punteggio, e «Apri il report». */
function RigaCheck({ group }: { group: CheckGroup }) {
  const { latest } = group;
  return (
    <li className="flex flex-col gap-2 border-b border-line px-2 py-4 md:flex-row md:items-center md:gap-6">
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <Link
          to={`/app/bandi/${group.slug}`}
          className="self-start rounded-mark text-row-title text-ink hover:text-accent-hover"
        >
          {latest.bando_titolo}
        </Link>
        <p className="text-small text-ink-3">
          {formatDateTime(latest.ready_at ?? latest.created_at)}
          {group.count > 1 && `, ${group.count} AI-check`}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {latest.status === "pending" ? (
          <span className="inline-flex items-center gap-2 text-small font-medium text-ink">
            <Spinner size="sm" />
            AI-check in corso…
          </span>
        ) : latest.status === "error" ? (
          <Status tono="attenzione">Non riuscita</Status>
        ) : (
          <>
            {latest.esito && <AiEsitoBadge esito={latest.esito} />}
            {latest.punteggio !== null && (
              <span className="text-figure-sm text-ink">
                {latest.punteggio}
                <span className="font-sans text-small font-normal text-ink-3">/100</span>
              </span>
            )}
          </>
        )}
        <TextLink to={`/app/bandi/${group.slug}#ai-check-report`} className="text-small font-medium">
          Apri il report
        </TextLink>
      </div>
    </li>
  );
}

export default function AiCheck() {
  const { data, isPending, isError, error, refetch } = useAiChecks();
  // Servono all'avviso per capire se un upgrade è davvero possibile: entrambe
  // sono già in cache TanStack (barra laterale e pagina Abbonamento).
  const { data: me } = useMe();
  const { data: plans } = usePlans();

  const groups = useMemo(() => groupBySlug(data?.items ?? []), [data]);
  const quota = data?.quota;

  return (
    <Page variante="elenco">
      <PageHeader
        titolo="AI-check"
        descrizione="Gli AI-check tra la tua azienda e i bandi: l'AI verifica ogni requisito citando il testo del bando e i tuoi dati."
      />

      {isPending ? (
        <div className="flex flex-col gap-6" aria-hidden>
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : isError ? (
        <ErrorState
          title="Non siamo riusciti a caricare gli AI-check."
          message={apiErrorMessage(error)}
          onRetry={() => refetch()}
        />
      ) : (
        <>
          {/* Quota del piano */}
          <div className="flex flex-col gap-2">
            {quota && quota.totale > 0 ? (
              <>
                <p className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-figure text-ink">{quota.rimanenti}</span>
                  <span className="text-body text-ink-2">
                    su {quota.totale} disponibili quest'anno
                  </span>
                </p>
                <ProgressBar
                  valore={quota.rimanenti}
                  massimo={quota.totale}
                  label="AI-check disponibili quest'anno"
                />
              </>
            ) : (
              <p className="text-body text-ink-2">
                Non inclusi nel tuo piano. <TextLink to="/app/abbonamento">Vedi i piani</TextLink>
              </p>
            )}
          </div>

          <QuotaUpgradeBanner quota={quota} me={me} plans={plans} />

          <Section>
            <SectionHeader titolo="I tuoi AI-check" />
            {groups.length === 0 ? (
              <EmptyState
                title="Nessun AI-check ancora."
                description="Apri un bando che ti interessa e avvia l'AI-check: troverai qui tutti i report."
                action={
                  <LinkButton to="/app/bandi" variant="secondary">
                    Cerca nei bandi
                  </LinkButton>
                }
              />
            ) : (
              <ul className="flex flex-col">
                {groups.map((group) => (
                  <RigaCheck key={group.slug} group={group} />
                ))}
              </ul>
            )}
            <p className="text-small text-ink-3">
              Report generati con l'AI a scopo orientativo: verifica sempre il testo ufficiale
              del bando prima di candidarti. Ogni nuovo AI-check ne consuma uno dal tuo piano.
            </p>
          </Section>
        </>
      )}
    </Page>
  );
}
