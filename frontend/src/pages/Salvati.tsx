import { Bookmark, CalendarCheck, CalendarPlus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { SavableBandoCard } from "../components/bandi/SavableBandoCard";
import { Badge } from "../components/ui/Badge";
import { Button, LinkButton } from "../components/ui/Button";
import { Pagination } from "../components/ui/Pagination";
import { BandoCardSkeleton, EmptyState, ErrorState } from "../components/ui/states";
import { useAddBandoDeadline } from "../hooks/useCalendar";
import { useSavedBandi, useToggleSaved } from "../hooks/useSavedBandi";
import { apiErrorMessage } from "../lib/api";
import { formatDate } from "../lib/format";
import type { SavedBandoItem } from "../types";

/** Card di ripiego per un bando salvato che non è più nel catalogo: niente
 *  link (il dettaglio darebbe 404), solo lo snapshot e la rimozione. */
function UnavailableCard({ item }: { item: SavedBandoItem }) {
  const toggle = useToggleSaved();
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50/60 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="amber">Non più disponibile</Badge>
      </div>
      <h3 className="mt-3 font-display text-base font-semibold text-slate-500">
        {item.bando.titolo ?? item.bando.slug}
      </h3>
      <p className="mt-1.5 text-sm text-slate-400">
        Questo bando non è più presente nel catalogo.
        {item.bando.data_scadenza && <> Scadeva il {formatDate(item.bando.data_scadenza)}.</>}
      </p>
      <div className="mt-4 flex items-center justify-between gap-2 border-t border-slate-200 pt-3">
        <span className="text-xs text-slate-400">Salvato il {formatDate(item.salvato_il)}</span>
        <Button
          variant="ghost"
          size="sm"
          className="text-red-600 hover:bg-red-50"
          loading={toggle.isPending}
          onClick={() =>
            toggle.mutate({ bando: { id: item.bando.id, slug: item.bando.slug }, save: false })
          }
        >
          <Trash2 className="size-4" aria-hidden />
          Rimuovi
        </Button>
      </div>
    </div>
  );
}

/** Azione «scadenza in calendario» sotto la card di un bando salvato. */
function CalendarAction({ item }: { item: SavedBandoItem }) {
  const addDeadline = useAddBandoDeadline();
  if (!item.bando.data_scadenza) return null;

  if (item.in_calendario || addDeadline.isSuccess) {
    return (
      <LinkButton
        to={`/app/calendario?m=${item.bando.data_scadenza.slice(0, 7)}`}
        variant="ghost"
        size="sm"
      >
        <CalendarCheck className="size-4 text-emerald-600" aria-hidden />
        Nel calendario
      </LinkButton>
    );
  }
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
        Aggiungi scadenza al calendario
      </Button>
      {addDeadline.isError && (
        <span className="text-xs text-red-600" role="alert">
          {apiErrorMessage(addDeadline.error)}
        </span>
      )}
    </>
  );
}

export default function Salvati() {
  const [page, setPage] = useState(1);
  const { data, isPending, isError, error, refetch, isPlaceholderData } = useSavedBandi(page);

  // Rimuovendo l'ultimo elemento di una pagina > 1 la pagina resterebbe
  // fuori intervallo (empty state fuorviante): si rientra sull'ultima piena.
  useEffect(() => {
    if (data && page > 1 && data.items.length === 0 && data.total > 0) {
      setPage(Math.max(1, data.total_pages));
    }
  }, [data, page]);

  return (
    <div>
      <h1 className="inline-flex items-center gap-2 font-display text-2xl font-bold tracking-tight text-slate-900">
        <Bookmark className="size-6 text-brand-500" aria-hidden />
        Bandi salvati
      </h1>
      <p className="mt-1 text-sm text-slate-500">
        {data ? (
          <>
            <span className="tabular font-medium text-slate-700">{data.total}</span>{" "}
            {data.total === 1 ? "bando salvato" : "bandi salvati"}
          </>
        ) : (
          "I bandi che hai messo da parte, sempre a portata di mano."
        )}
      </p>

      <section className="mt-6" aria-busy={isPending || isPlaceholderData}>
        {isPending ? (
          <div className="grid gap-4 xl:grid-cols-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <BandoCardSkeleton key={i} />
            ))}
          </div>
        ) : isError ? (
          <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
        ) : data && data.items.length === 0 ? (
          <EmptyState
            title="Nessun bando salvato"
            description="Sfoglia il catalogo e usa il segnalibro sulle card per mettere da parte i bandi che ti interessano."
            action={<LinkButton to="/app/bandi">Esplora i bandi</LinkButton>}
          />
        ) : (
          <>
            <div
              className={
                "grid gap-4 xl:grid-cols-2" +
                (isPlaceholderData ? " opacity-60 transition-opacity" : "")
              }
            >
              {data?.items.map((item) =>
                item.disponibile ? (
                  <div key={item.bando.id} className="flex h-full flex-col gap-1.5">
                    <SavableBandoCard bando={item.bando} className="flex-1" />
                    <div className="flex flex-wrap items-center gap-2 px-1">
                      <CalendarAction item={item} />
                    </div>
                  </div>
                ) : (
                  <UnavailableCard key={item.bando.id} item={item} />
                ),
              )}
            </div>
            <div className="mt-8">
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
    </div>
  );
}
