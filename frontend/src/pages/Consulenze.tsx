import { CalendarClock } from "lucide-react";
import { Link } from "react-router-dom";
import { inizioAppuntamento } from "../components/consulenze/formato";
import { Badge } from "../components/ui/Badge";
import { LinkButton } from "../components/ui/Button";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { Status, type TonoStatus } from "../components/ui/Status";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { useConsulenze } from "../hooks/useConsulenze";
import { useFunzioni } from "../hooks/useFunzioni";
import { apiErrorMessage } from "../lib/api";
import {
  CONSULENZA_STATO_LABELS,
  CONSULTO_CALL_COPY,
  PROPOSTA_STATO_LABELS,
} from "../lib/copy";
import { formatDate } from "../lib/format";
import type { Consulenza, ConsulenzaStato, PropostaStato } from "../types";

const TONI_CONSULENZA: Record<ConsulenzaStato, TonoStatus> = {
  nuova: "in-apertura",
  assegnata: "aperto",
  annullata: "chiuso",
};

/** Lo stato della consulenza in parole (punto + parola), lato cliente e lato
 *  progettista. */
export function ConsulenzaStatoBadge({ stato }: { stato: ConsulenzaStato }) {
  return <Status tono={TONI_CONSULENZA[stato]}>{CONSULENZA_STATO_LABELS[stato]}</Status>;
}

// Una proposta rifiutata non è un errore: niente rosso, come le altre chiuse.
const TONI_PROPOSTA: Record<PropostaStato, TonoStatus> = {
  inviata: "in-apertura",
  accettata: "aperto",
  rifiutata: "chiuso",
  superata: "chiuso",
  ritirata: "chiuso",
};

/** Lo stato di una proposta in parole; `prefisso` per le righe del progettista
 *  («Proposta inviata»). */
export function PropostaStatoBadge({
  stato,
  prefisso,
}: {
  stato: PropostaStato;
  prefisso?: string;
}) {
  const parola = PROPOSTA_STATO_LABELS[stato];
  return (
    <Status tono={TONI_PROPOSTA[stato]}>
      {prefisso ? `${prefisso} ${parola.toLowerCase()}` : parola}
    </Status>
  );
}

/** Etichetta delle consulenze chieste dalla call di partenariato (WP9). */
export function ConsultoCallBadge() {
  return <Badge>{CONSULTO_CALL_COPY.badge}</Badge>;
}

/** Link alla call di una consulenza chiesta dalla call (WP9); null se la
 *  consulenza viene dall'AI-check o se il modulo partenariati è spento (la call
 *  non si apre, e non se ne parla). `?azienda=` attiva l'azienda che ha
 *  creato la call, l'unica con cui la call si apre. */
export function linkCallDelConsulto(consulenza: Consulenza, partenariatiAttivo: boolean) {
  if (!partenariatiAttivo || !consulenza.partner_call_id) return null;
  const azienda = consulenza.company_profile_id
    ? `&azienda=${encodeURIComponent(consulenza.company_profile_id)}`
    : "";
  return `/app/partenariati/call/${consulenza.partner_call_id}?tab=panoramica${azienda}`;
}

function proposteDaValutare(n: number): string {
  if (n === 0) return "Nessuna proposta ricevuta finora";
  return n === 1 ? "1 proposta da valutare" : `${n} proposte da valutare`;
}

/** Riga dell'elenco: il bando è il link al dettaglio (niente pulsanti nella
 *  riga); sotto lo stato e i metadati, a destra le proposte o l'appuntamento. */
function RigaConsulenza({
  consulenza,
  partenariatiAttivo,
}: {
  consulenza: Consulenza;
  partenariatiAttivo: boolean;
}) {
  return (
    <li className="flex flex-col gap-2 border-b border-line px-2 py-4 md:flex-row md:items-start md:gap-6">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <Link
          to={`/app/consulenze/${consulenza.id}`}
          className="self-start rounded-mark text-row-title text-ink hover:text-accent-hover"
        >
          {consulenza.bando_titolo}
        </Link>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-small text-ink-2">
          <ConsulenzaStatoBadge stato={consulenza.stato} />
          <span>Richiesta del {formatDate(consulenza.created_at)}</span>
          {consulenza.progettista?.nome && (
            <span>
              Progettista{" "}
              <span className="font-medium text-ink">{consulenza.progettista.nome}</span>
            </span>
          )}
          {/* Il link alla call sta nel dettaglio: qui il titolo è già un link. */}
          {linkCallDelConsulto(consulenza, partenariatiAttivo) && <ConsultoCallBadge />}
        </div>
      </div>

      {(consulenza.stato === "nuova" || consulenza.appuntamento) && (
        <div className="flex shrink-0 flex-col gap-1 text-small md:w-64 md:items-end md:text-right">
          {consulenza.stato === "nuova" && (
            <span
              className={
                consulenza.proposte_aperte > 0 ? "font-semibold text-ink" : "text-ink-2"
              }
            >
              {proposteDaValutare(consulenza.proposte_aperte)}
            </span>
          )}
          {consulenza.appuntamento && (
            <span className="inline-flex items-center gap-1.5 text-ink tabular-nums">
              <CalendarClock className="size-4 shrink-0 text-ink-3" aria-hidden />
              <time dateTime={consulenza.appuntamento.inizio}>
                {inizioAppuntamento(consulenza.appuntamento.inizio)}
              </time>
            </span>
          )}
        </div>
      )}
    </li>
  );
}

function RigaSkeleton() {
  return (
    <li className="flex flex-col gap-2 border-b border-line px-2 py-4" aria-hidden>
      <Skeleton className="h-5 w-3/5" />
      <div className="flex gap-3">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-3 w-28" />
      </div>
    </li>
  );
}

/** Le richieste di consulenza dell'Azienda: elenco a righe, dettaglio a parte. */
export default function Consulenze() {
  const { data: consulenze, isPending, isError, error, refetch } = useConsulenze();
  const { partenariatiAttivo } = useFunzioni();

  return (
    <Page variante="elenco">
      <PageHeader
        titolo="Consulenze"
        descrizione="Le tue richieste di consulenza con i progettisti: dalle proposte ricevute all'appuntamento."
      />

      {isPending ? (
        <ul className="flex flex-col border-t border-line">
          {Array.from({ length: 3 }).map((_, i) => (
            <RigaSkeleton key={i} />
          ))}
        </ul>
      ) : isError ? (
        <ErrorState
          title="Non siamo riusciti a caricare le consulenze."
          message={apiErrorMessage(error)}
          onRetry={() => refetch()}
        />
      ) : !consulenze || consulenze.length === 0 ? (
        <EmptyState
          title="Nessuna richiesta di consulenza"
          description="Completa un AI-check su un bando e richiedi una consulenza dalla scheda del bando: la tua richiesta arriverà ai progettisti della piattaforma."
          action={
            <LinkButton to="/app/ai-check" variant="secondary">
              Vai agli AI-check
            </LinkButton>
          }
        />
      ) : (
        <ul className="flex flex-col border-t border-line">
          {consulenze.map((consulenza) => (
            <RigaConsulenza
              key={consulenza.id}
              consulenza={consulenza}
              partenariatiAttivo={partenariatiAttivo}
            />
          ))}
        </ul>
      )}
    </Page>
  );
}
