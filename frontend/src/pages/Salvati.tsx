import { CalendarCheck, CalendarPlus } from "lucide-react";
import { useEffect, useState } from "react";
import { BandoRow, BandoRowSkeleton } from "../components/bandi/BandoRow";
import { bandoInCorso, statoDelBando } from "../components/bandi/stato";
import { Button, LinkButton } from "../components/ui/Button";
import { Due } from "../components/ui/Due";
import { InlineError } from "../components/ui/InlineError";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { Pagination } from "../components/ui/Pagination";
import { Status } from "../components/ui/Status";
import { EmptyState, ErrorState } from "../components/ui/states";
import { useAddBandoDeadline } from "../hooks/useCalendar";
import { useSavedBandi, useToggleSaved } from "../hooks/useSavedBandi";
import { apiErrorMessage } from "../lib/api";
import { cn } from "../lib/cn";
import { formatDate } from "../lib/format";
import type { SavedBandoItem } from "../types";

/** Riga di ripiego per un bando salvato che il catalogo non restituisce:
 *  niente link al vecchio dettaglio (darebbe 404), solo lo snapshot e la
 *  rimozione. Testo neutro (il bando potrebbe tornare), tranne quando si
 *  conosce la scheda che lo sostituisce (`slug_aggiornato`): allora lo stato e
 *  il testo lo dicono e c'è il link. */
function RigaNonDisponibile({ item }: { item: SavedBandoItem }) {
  const toggle = useToggleSaved();
  return (
    <li className="flex items-start gap-4 border-b border-line px-2 py-4 md:gap-6">
      {/* Scheda non disponibile: solo la data, senza conto alla rovescia. */}
      <Due data={item.bando.data_scadenza} conConto={false} />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className="text-row-title text-ink-2">{item.bando.titolo ?? item.bando.slug}</p>
        <p className="text-body text-ink-2">
          {item.slug_aggiornato
            ? "Questo bando è stato unito a un'altra scheda del catalogo. Qui trovi i dati che avevi salvato."
            : "Al momento non riusciamo a mostrare la scheda aggiornata di questo bando. Qui trovi i dati che avevi salvato."}
        </p>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <Status tono="neutro">
            {item.slug_aggiornato ? "Unita a un'altra scheda" : "Scheda non disponibile"}
          </Status>
          <span className="text-caption text-ink-3">Salvato il {formatDate(item.salvato_il)}</span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          {item.slug_aggiornato && (
            <LinkButton to={`/app/bandi/${item.slug_aggiornato}`} variant="ghost" size="sm">
              Apri la scheda aggiornata
            </LinkButton>
          )}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            loading={toggle.isPending}
            onClick={() =>
              toggle.mutate({ bando: { id: item.bando.id, slug: item.bando.slug }, save: false })
            }
          >
            Rimuovi dai bandi salvati
          </Button>
        </div>
      </div>
    </li>
  );
}

/** C'è un'azione del calendario da mostrare? Con la scadenza, se il bando è già
 *  nel calendario («Nel calendario») o se è in corso («Aggiungi…»). Si decide
 *  qui, prima di passare `azioni` a `BandoRow`: un componente che restituisce
 *  `null` lascerebbe comunque il contenitore vuoto della riga. */
function haAzioneCalendario(item: SavedBandoItem): boolean {
  if (!item.bando.data_scadenza) return false;
  return item.in_calendario || bandoInCorso(statoDelBando(item.bando));
}

/** «Aggiungi la scadenza al calendario» sotto la riga di un bando salvato: si
 *  aggiunge solo a bando in corso; «Nel calendario» resta comunque visibile. */
function AzioneCalendario({ item }: { item: SavedBandoItem }) {
  const addDeadline = useAddBandoDeadline();
  if (!item.bando.data_scadenza) return null;

  if (item.in_calendario || addDeadline.isSuccess) {
    return (
      <LinkButton
        to={`/app/calendario?m=${item.bando.data_scadenza.slice(0, 7)}`}
        variant="ghost"
        size="sm"
      >
        <CalendarCheck className="size-4" aria-hidden />
        Nel calendario
      </LinkButton>
    );
  }
  if (!bandoInCorso(statoDelBando(item.bando))) return null;
  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        loading={addDeadline.isPending}
        onClick={() => addDeadline.mutate(item.bando.slug)}
      >
        <CalendarPlus className="size-4" aria-hidden />
        Aggiungi la scadenza al calendario
      </Button>
      {addDeadline.isError && <InlineError>{apiErrorMessage(addDeadline.error)}</InlineError>}
    </>
  );
}

export default function Salvati() {
  const [page, setPage] = useState(1);
  const { data, isPending, isError, error, refetch, isPlaceholderData } = useSavedBandi(page);

  // Rimuovendo l'ultimo elemento di una pagina > 1 la pagina resterebbe
  // fuori intervallo (stato vuoto fuorviante): si rientra sull'ultima piena.
  useEffect(() => {
    if (data && page > 1 && data.items.length === 0 && data.total > 0) {
      setPage(Math.max(1, data.total_pages));
    }
  }, [data, page]);

  const descrizione = data
    ? `${data.total.toLocaleString("it-IT")} ${data.total === 1 ? "bando salvato" : "bandi salvati"}`
    : "I bandi che hai messo da parte, sempre a portata di mano.";

  return (
    <Page variante="elenco">
      <PageHeader titolo="Bandi salvati" descrizione={descrizione} />

      <section aria-label="Bandi salvati" aria-busy={isPending || isPlaceholderData}>
        {isPending ? (
          <ul className="flex flex-col border-t border-line">
            {Array.from({ length: 3 }).map((_, i) => (
              <BandoRowSkeleton key={i} />
            ))}
          </ul>
        ) : isError ? (
          <ErrorState
            title="Non siamo riusciti a caricare i bandi salvati."
            message={apiErrorMessage(error)}
            onRetry={() => refetch()}
          />
        ) : data && data.items.length === 0 ? (
          <EmptyState
            title="Non hai ancora salvato nessun bando."
            description="Sfoglia i bandi e usa il segnalibro per mettere da parte quelli che ti interessano: li ritrovi qui."
            action={
              <LinkButton to="/app/bandi" variant="secondary">
                Cerca nei bandi
              </LinkButton>
            }
          />
        ) : (
          <>
            <ul
              className={cn(
                "flex flex-col border-t border-line",
                isPlaceholderData && "opacity-60 transition-opacity",
              )}
            >
              {data?.items.map((item) =>
                item.disponibile ? (
                  <BandoRow
                    key={item.bando.id}
                    bando={item.bando}
                    azioni={
                      haAzioneCalendario(item) ? <AzioneCalendario item={item} /> : undefined
                    }
                  />
                ) : (
                  <RigaNonDisponibile key={item.bando.id} item={item} />
                ),
              )}
            </ul>
            <div className="mt-6">
              <Pagination
                page={page}
                totalPages={data?.total_pages ?? 1}
                onChange={(next) => {
                  setPage(next);
                  window.scrollTo({ top: 0, behavior: "smooth" });
                }}
              />
            </div>
          </>
        )}
      </section>
    </Page>
  );
}
