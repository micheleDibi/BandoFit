import { CalendarClock, Inbox } from "lucide-react";
import { Link } from "react-router-dom";
import { ConsulenzaStatoBadge, PropostaStatoBadge } from "../Consulenze";
import { AiEsitoBadge } from "../../components/bandi/badges";
import { inizioAppuntamento } from "../../components/consulenze/formato";
import { Badge } from "../../components/ui/Badge";
import { Card } from "../../components/ui/Card";
import { LINK_ESTESO } from "../../components/shared/linkEsteso";
import { Page } from "../../components/ui/Page";
import { PageHeader } from "../../components/ui/PageHeader";
import { Section, SectionHeader } from "../../components/ui/SectionHeader";
import { EmptyState, ErrorState, Skeleton } from "../../components/ui/states";
import { useFunzioni } from "../../hooks/useFunzioni";
import { useRichiestePool } from "../../hooks/useProgettistaRichieste";
import { apiErrorMessage } from "../../lib/api";
import { formatDate } from "../../lib/format";
import type { RichiestaPool } from "../../types";

/** Titolo della richiesta: per una consulenza chiesta dalla call di
 *  partenariato non ancora assegnata a chi guarda il server non manda i dati
 *  dell'azienda (solo il bando). */
export function titoloRichiesta(richiesta: RichiestaPool): string {
  if (richiesta.da_call && !richiesta.assegnata_a_me && !richiesta.ragione_sociale) {
    return "Consulenza su una call di partenariato";
  }
  return richiesta.ragione_sociale ?? richiesta.denominazione_utente;
}

/** Etichetta «Call di partenariato». */
export function BadgeDaCall() {
  return <Badge area="partenariati">Call di partenariato</Badge>;
}

/** Card della richiesta (tutta cliccabile, bordo dell'area consulenze): il
 *  titolo è il link al dettaglio; un solo stato
 *  (della consulenza se è assegnata a chi guarda, altrimenti della sua
 *  proposta); a destra esito e punteggio dell'AI-check e l'appuntamento. */
function RigaRichiesta({ richiesta }: { richiesta: RichiestaPool }) {
  const conAiCheck = !!richiesta.esito || richiesta.punteggio !== null;
  return (
    <li>
      <Card
        interattiva
        area="consulenze"
        className="relative flex flex-col gap-2 md:flex-row md:items-start md:gap-6"
      >
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <Link
            to={`/app/progettista/richieste/${richiesta.id}`}
            className={`self-start rounded-mark text-row-title text-ink hover:text-accent-hover ${LINK_ESTESO}`}
          >
            {titoloRichiesta(richiesta)}
          </Link>
          <p className="text-body text-ink-2">{richiesta.bando_titolo}</p>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-small text-ink-2">
            {richiesta.assegnata_a_me ? (
              <ConsulenzaStatoBadge stato={richiesta.stato} />
            ) : richiesta.mia_proposta_stato ? (
              <PropostaStatoBadge stato={richiesta.mia_proposta_stato} prefisso="Proposta" />
            ) : null}
            {richiesta.partita_iva && (
              <span className="tabular-nums">P.IVA {richiesta.partita_iva}</span>
            )}
            <span>Richiesta del {formatDate(richiesta.created_at)}</span>
            {richiesta.da_call && <BadgeDaCall />}
          </div>
        </div>

        {(conAiCheck || richiesta.appuntamento) && (
          <div className="flex shrink-0 flex-col gap-1.5 text-small md:w-64 md:items-end md:text-right">
            {conAiCheck && (
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 md:justify-end">
                {richiesta.esito && <AiEsitoBadge esito={richiesta.esito} />}
                {richiesta.punteggio !== null && (
                  <span className="text-figure-sm text-ink">
                    <span className="sr-only">Punteggio dell'AI-check: </span>
                    {richiesta.punteggio}
                    <span className="font-sans text-small font-normal text-ink-3">/100</span>
                  </span>
                )}
              </div>
            )}
            {richiesta.appuntamento && (
              <span className="inline-flex items-center gap-1.5 text-ink tabular-nums">
                <CalendarClock className="size-4 shrink-0 text-area-consulenze-ink" aria-hidden />
                <time dateTime={richiesta.appuntamento.inizio}>
                  {inizioAppuntamento(richiesta.appuntamento.inizio)}
                </time>
              </span>
            )}
          </div>
        )}
      </Card>
    </li>
  );
}

function RigaSkeleton() {
  return (
    <li aria-hidden>
      <Card className="flex flex-col gap-2">
        <Skeleton className="h-5 w-2/5" />
        <Skeleton className="h-4 w-3/5" />
        <div className="flex gap-3">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-3 w-28" />
        </div>
      </Card>
    </li>
  );
}

/** Le richieste di consulenza: quelle aperte a tutti i progettisti e quelle
 *  assegnate a chi guarda (progettista e admin, con gli stessi poteri). */
export default function Richieste() {
  const { data, isPending, isError, error, refetch } = useRichiestePool();
  const { partenariatiAttivo } = useFunzioni();

  return (
    <Page variante="elenco">
      <PageHeader
        area="consulenze"
        titolo="Richieste di consulenza"
        descrizione={`${
          partenariatiAttivo
            ? "Le aziende che hanno chiesto una consulenza su un bando, dopo un AI-check o dalla loro call di partenariato."
            : "Le aziende che hanno chiesto una consulenza dopo un AI-check."
        } Invia una proposta: se il titolare la accetta, la consulenza è assegnata a te.`}
      />

      {isPending ? (
        <ul className="flex flex-col gap-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <RigaSkeleton key={i} />
          ))}
        </ul>
      ) : isError ? (
        <ErrorState
          title="Non siamo riusciti a caricare le richieste di consulenza."
          message={apiErrorMessage(error)}
          onRetry={() => refetch()}
        />
      ) : (
        <>
          {data && data.assegnate.length > 0 && (
            <Section aria-label="Consulenze assegnate a te">
              <SectionHeader titolo="Assegnate a te" />
              <ul className="flex flex-col gap-3">
                {data.assegnate.map((r) => (
                  <RigaRichiesta key={r.id} richiesta={r} />
                ))}
              </ul>
            </Section>
          )}

          <Section aria-label="Richieste aperte">
            <SectionHeader titolo="Richieste aperte" />
            {!data || data.aperte.length === 0 ? (
              <EmptyState
                icon={Inbox}
                area="consulenze"
                title="Nessuna richiesta aperta"
                description="Quando un'azienda chiederà una consulenza la troverai qui (e riceverai una notifica)."
              />
            ) : (
              <ul className="flex flex-col gap-3">
                {data.aperte.map((r) => (
                  <RigaRichiesta key={r.id} richiesta={r} />
                ))}
              </ul>
            )}
          </Section>
        </>
      )}
    </Page>
  );
}
