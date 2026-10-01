import { useMemo } from "react";
import { Link } from "react-router-dom";
import { useSavedBandi } from "../../hooks/useSavedBandi";
import { bandoInCorso, statoDelBando } from "../bandi/stato";
import { LinkButton } from "../ui/Button";
import { Due } from "../ui/Due";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { EmptyState, ErrorState, Skeleton } from "../ui/states";
import { TextLink } from "../ui/TextLink";

/** I bandi salvati più recenti (la prima pagina: 20), in ordine di scadenza. */
const PAGINA_SALVATI = 20;

/** «Prossime scadenze» della Home: solo i bandi salvati, disponibili, in corso
 *  e con una data, dalla scadenza più vicina. Il limite ai 20 salvati più di
 *  recente si dice sotto l'elenco. */
export function ProssimeScadenze() {
  const { data, isPending, isError, refetch } = useSavedBandi(1);

  const righe = useMemo(
    () =>
      (data?.items ?? [])
        .filter(
          (item) =>
            item.disponibile &&
            item.bando.data_scadenza !== null &&
            bandoInCorso(statoDelBando(item.bando)),
        )
        .sort((a, b) => a.bando.data_scadenza!.localeCompare(b.bando.data_scadenza!)),
    [data],
  );

  return (
    <Section aria-label="Prossime scadenze">
      <SectionHeader
        titolo="Prossime scadenze"
        azione={
          <TextLink to="/app/salvati" className="text-small font-medium">
            Vedi i bandi salvati
          </TextLink>
        }
      />
      {isPending ? (
        <ul className="flex flex-col border-t border-line" aria-hidden>
          {Array.from({ length: 3 }).map((_, i) => (
            <li key={i} className="flex items-start gap-4 border-b border-line px-2 py-4">
              <div className="flex w-18 shrink-0 flex-col gap-1.5">
                <Skeleton className="h-7 w-8" />
                <Skeleton className="h-3 w-14" />
              </div>
              <div className="flex flex-1 flex-col gap-2">
                <Skeleton className="h-5 w-3/5" />
                <Skeleton className="h-3 w-32" />
              </div>
            </li>
          ))}
        </ul>
      ) : isError ? (
        <ErrorState
          title="Non siamo riusciti a caricare le scadenze."
          onRetry={() => refetch()}
        />
      ) : righe.length === 0 ? (
        <EmptyState
          title="Non hai bandi salvati con una scadenza."
          description="Salva un bando aperto e lo ritrovi qui, in ordine di scadenza."
          action={
            <LinkButton to="/app/bandi" variant="secondary">
              Cerca nei bandi
            </LinkButton>
          }
        />
      ) : (
        <ul className="flex flex-col border-t border-line">
          {righe.map((item) => (
            <li
              key={item.bando.id}
              className="flex items-start gap-4 border-b border-line px-2 py-4 md:gap-6"
            >
              <Due data={item.bando.data_scadenza} />
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <Link
                  to={`/app/bandi/${item.bando.slug}`}
                  className="self-start rounded-mark text-row-title text-ink hover:text-accent-hover"
                >
                  {item.bando.titolo ?? item.bando.titolo_breve ?? "Bando senza titolo"}
                </Link>
                {item.bando.ente_erogatore && (
                  <p className="text-small text-ink-2">{item.bando.ente_erogatore}</p>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {data && data.total > PAGINA_SALVATI && (
        <p className="text-small text-ink-3">
          Contiamo i {PAGINA_SALVATI} bandi salvati più di recente.{" "}
          <TextLink to="/app/salvati">Vedi tutti i bandi salvati</TextLink>
        </p>
      )}
    </Section>
  );
}
