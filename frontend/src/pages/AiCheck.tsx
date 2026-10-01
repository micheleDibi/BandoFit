import { Sparkles } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";
import { QuotaUpgradeBanner } from "../components/aicheck/QuotaUpgradeBanner";
import { AiEsitoBadge } from "../components/bandi/badges";
import { LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { IconChip } from "../components/ui/IconChip";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { ProgressRing } from "../components/ui/ProgressRing";
import { Section, SectionHeader } from "../components/ui/SectionHeader";
import { Spinner } from "../components/ui/Spinner";
import { Status } from "../components/ui/Status";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { TextLink } from "../components/ui/TextLink";
import { useAiChecks } from "../hooks/useAiCheck";
import { useMe } from "../hooks/useMe";
import { usePlans } from "../hooks/usePlans";
import { apiErrorMessage } from "../lib/api";
import { cn } from "../lib/cn";
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

/** Bordo sinistro della card per esito (il colore accompagna sempre la parola
 *  del badge o dello stato): in linea verde, dati da completare ambra, in
 *  corso blu, non riuscita rossa (è un errore, come lo stato `errore`);
 *  l'esito negativo resta neutro, mai rosso. */
function bordoCheck(check: AiCheck): string {
  if (check.status === "pending") return "border-l-4 border-l-accent";
  if (check.status === "error") return "border-l-4 border-l-danger";
  if (check.esito === "ammissibile") return "border-l-4 border-l-fit";
  if (check.esito === "da_verificare") return "border-l-4 border-l-warning";
  return "border-l-4 border-l-ink-off";
}

/** Card dello storico: il punteggio in un anello, il bando (il titolo è il
 *  link e copre la card), la data dell'ultima analisi, lo stato o l'esito, e
 *  «Apri il report» sopra il link della card. */
function RigaCheck({ group }: { group: CheckGroup }) {
  const { latest } = group;
  const punteggio = latest.status === "ready" ? latest.punteggio : null;
  return (
    <li>
      <Card
        interattiva
        className={cn(
          "relative flex items-center gap-4 p-4 md:gap-6 md:p-5",
          bordoCheck(latest),
        )}
      >
        {punteggio !== null ? (
          <ProgressRing
            value={punteggio}
            max={100}
            size={56}
            tono="fit"
            label={`Punteggio di compatibilità: ${punteggio} su 100`}
          >
            {punteggio}
          </ProgressRing>
        ) : (
          <IconChip icon={Sparkles} area="aicheck" size="lg" className="size-14" />
        )}

        <div className="flex min-w-0 flex-1 flex-col gap-2 md:flex-row md:items-center md:gap-6">
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <Link
              to={`/app/bandi/${group.slug}`}
              className={cn(
                "self-start rounded-mark text-row-title text-ink hover:text-accent-hover",
                // Il link copre la card: l'anello del focus si disegna sulla card intera.
                "after:absolute after:inset-0 after:rounded-panel",
                "focus-visible:outline-none focus-visible:after:outline-solid focus-visible:after:outline-2",
                "focus-visible:after:outline-offset-2 focus-visible:after:outline-accent",
              )}
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
              <Status tono="errore">Non riuscita</Status>
            ) : (
              <>
                {latest.esito && <AiEsitoBadge esito={latest.esito} />}
                {latest.punteggio !== null && (
                  <span className="text-small text-ink-3 tabular-nums" aria-hidden>
                    {latest.punteggio} su 100
                  </span>
                )}
              </>
            )}
            <TextLink
              to={`/app/bandi/${group.slug}#ai-check-report`}
              className="relative z-10 text-small font-medium"
            >
              Apri il report
            </TextLink>
          </div>
        </div>
      </Card>
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
        area="aicheck"
        titolo="AI-check"
        descrizione="Gli AI-check tra la tua azienda e i bandi: l'AI verifica ogni requisito citando il testo del bando e i tuoi dati."
      />

      {isPending ? (
        <div className="flex flex-col gap-6" aria-hidden>
          <Skeleton className="h-28 w-full rounded-panel" />
          <Skeleton className="h-40 w-full rounded-panel" />
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
          <Card className="flex items-center gap-5">
            {quota && quota.totale > 0 ? (
              <>
                {/* Il numero grande è al centro dell'anello; la frase intera la
                    legge lo screen reader dal nome dell'anello. */}
                <ProgressRing
                  value={quota.rimanenti}
                  max={quota.totale}
                  size={88}
                  tono="aicheck"
                  label={`AI-check disponibili quest'anno: ${quota.rimanenti} su ${quota.totale}`}
                />
                <p className="text-body text-ink-2" aria-hidden>
                  su {quota.totale} disponibili quest'anno
                </p>
              </>
            ) : (
              <>
                <IconChip icon={Sparkles} area="aicheck" size="lg" />
                <p className="text-body text-ink-2">
                  Non inclusi nel tuo piano. <TextLink to="/app/abbonamento">Vedi i piani</TextLink>
                </p>
              </>
            )}
          </Card>

          <QuotaUpgradeBanner quota={quota} me={me} plans={plans} />

          <Section>
            <SectionHeader titolo="I tuoi AI-check" />
            {groups.length === 0 ? (
              <EmptyState
                title="Nessun AI-check ancora."
                description="Apri un bando che ti interessa e avvia l'AI-check: troverai qui tutti i report."
                area="aicheck"
                action={
                  <LinkButton to="/app/bandi" variant="secondary">
                    Cerca nei bandi
                  </LinkButton>
                }
              />
            ) : (
              <ul className="flex flex-col gap-3">
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
