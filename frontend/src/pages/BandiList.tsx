import { ListFilter, SearchX } from "lucide-react";
import { useEffect, useState } from "react";
import { ActiveFilterChips } from "../components/bandi/ActiveFilterChips";
import { AltriFiltriDrawer } from "../components/bandi/AltriFiltri";
import { BandiPerTeSegment } from "../components/bandi/BandiPerTeButton";
import { BandoRow, BandoRowSkeleton } from "../components/bandi/BandoRow";
import { FiltriBandi } from "../components/bandi/FiltriBandi";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { Pagination } from "../components/ui/Pagination";
import { SearchInput } from "../components/ui/SearchInput";
import { Select } from "../components/ui/Select";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { useBandi } from "../hooks/useBandi";
import { useBandiFilters } from "../hooks/useBandiFilters";
import { useDebounce } from "../hooks/useDebounce";
import { useLookups } from "../hooks/useLookups";
import { apiErrorMessage } from "../lib/api";
import { cn } from "../lib/cn";

// Il backend mette sempre i bandi chiusi in coda, qualunque ordinamento.
const SORT_LABELS: Record<string, string> = {
  pubblicazione_desc: "Più recenti",
  scadenza_asc: "Scadenza più vicina",
  scadenza_desc: "Scadenza più lontana",
  importo_desc: "Importo più alto",
};

function contaBandi(n: number): string {
  return `${n.toLocaleString("it-IT")} ${n === 1 ? "bando" : "bandi"}`;
}

/** Il conteggio dei risultati sopra l'elenco: il numero in evidenza, poi la
 *  stessa frase dell'intestazione. */
function ConteggioRisultati({
  totale,
  conFiltri,
  attenuato,
}: {
  totale: number;
  conFiltri: boolean;
  /** Dati della ricerca precedente mentre arriva la nuova: attenuato come l'elenco. */
  attenuato: boolean;
}) {
  return (
    <p
      className={cn(
        "flex flex-wrap items-baseline gap-x-2 text-body text-ink-2",
        attenuato && "opacity-60 transition-opacity",
      )}
    >
      <span className="text-figure-sm text-ink">{totale.toLocaleString("it-IT")}</span>
      <span>
        {totale === 1 ? "bando" : "bandi"} {conFiltri ? "con i filtri scelti" : "nel catalogo"}
      </span>
    </p>
  );
}

export default function BandiList() {
  const { filters, update, toggleFacet, reset, activeCount } = useBandiFilters();
  const { data: lookups } = useLookups();
  const { data, isPending, isError, error, refetch, isPlaceholderData } = useBandi(filters);
  const [filtriAperti, setFiltriAperti] = useState(false);

  // Pagina oltre l'ultima (URL a mano, link vecchio, totale calato): si
  // rientra sull'ultima piena. Mai sui dati segnaposto della query precedente.
  useEffect(() => {
    if (
      data &&
      !isPlaceholderData &&
      filters.page > 1 &&
      data.items.length === 0 &&
      data.total > 0
    ) {
      update({ page: Math.max(1, data.total_pages) }, { keepPage: true });
    }
  }, [data, isPlaceholderData, filters.page, update]);

  // Ricerca con debounce: stato locale → URL dopo 400ms.
  const [searchInput, setSearchInput] = useState(filters.q);
  const debouncedSearch = useDebounce(searchInput, 400);
  useEffect(() => {
    if (debouncedSearch !== filters.q) update({ q: debouncedSearch });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch]);
  // Se i filtri vengono azzerati dall'esterno, riallinea l'input.
  useEffect(() => {
    if (filters.q === "" && searchInput !== "" && debouncedSearch === searchInput) {
      setSearchInput("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.q]);

  const toggleStato = (stato: string) => {
    const next = filters.stato.includes(stato)
      ? filters.stato.filter((s) => s !== stato)
      : [...filters.stato, stato];
    update({ stato: next });
  };

  const azzera = () => {
    setSearchInput("");
    reset();
  };

  // Scelte attive che stanno solo nel cassetto: per il conteggio su «Altri filtri».
  const altriAttivi =
    filters.settori.length +
    filters.ateco.length +
    filters.modalita.length +
    filters.programmi.length +
    (filters.partenariato !== null ? 1 : 0);

  const descrizione = data
    ? activeCount > 0
      ? `${contaBandi(data.total)} con i filtri scelti`
      : `${contaBandi(data.total)} nel catalogo`
    : "Esplora il catalogo dei bandi attivi";

  const controlli = {
    filters,
    onToggleFacet: toggleFacet,
    onToggleStato: toggleStato,
    onUpdate: update,
  };

  return (
    <Page variante="elenco">
      <PageHeader area="bandi" titolo="Bandi" descrizione={descrizione} />

      <Card className="flex flex-col gap-3 p-4 sm:p-5">
        {/* Barra: ricerca, segmento del preset, ordinamento (e «Filtri» sotto lg). */}
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            value={searchInput}
            onChange={setSearchInput}
            placeholder="Cerca per parola chiave, per esempio «digitalizzazione»"
            label="Cerca nei bandi"
            className="flex-1"
          />
          <BandiPerTeSegment className="w-full [&>button]:flex-1 lg:w-auto lg:[&>button]:flex-none" />
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="secondary"
              className="h-9 lg:hidden"
              onClick={() => setFiltriAperti(true)}
              aria-haspopup="dialog"
              aria-expanded={filtriAperti}
            >
              <ListFilter className="size-4" aria-hidden />
              Filtri
              {activeCount > 0 && (
                <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-pill bg-accent px-1.5 text-caption text-on-accent tabular-nums">
                  {activeCount}
                </span>
              )}
            </Button>
            <span className="hidden whitespace-nowrap text-small text-ink-2 lg:inline" aria-hidden>
              Ordina per
            </span>
            <Select
              id="sort-select"
              label="Ordina per"
              value={filters.sort}
              onChange={(e) => update({ sort: e.target.value })}
              className="flex-1 lg:flex-none"
            >
              {Object.entries(SORT_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </div>
        </div>

        <FiltriBandi
          {...controlli}
          lookups={lookups}
          onReset={azzera}
          activeCount={activeCount}
          altriAttivi={altriAttivi}
          altriAperti={filtriAperti}
          onAltri={() => setFiltriAperti(true)}
          className="hidden border-t border-line pt-3 lg:flex"
        />

        <ActiveFilterChips
          filters={filters}
          lookups={lookups}
          onToggleFacet={toggleFacet}
          onToggleStato={toggleStato}
          onUpdate={update}
          onReset={azzera}
        />
      </Card>

      <AltriFiltriDrawer
        {...controlli}
        open={filtriAperti}
        onClose={() => setFiltriAperti(false)}
        lookups={lookups}
        onReset={azzera}
        activeCount={activeCount}
        totale={data?.total}
      />

      <section
        aria-label="Risultati"
        aria-busy={isPending || isPlaceholderData}
        className="flex flex-col gap-4"
      >
        {isPending ? (
          <>
            <Skeleton className="h-6 w-40" />
            <ul className="flex flex-col gap-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <BandoRowSkeleton key={i} />
              ))}
            </ul>
          </>
        ) : isError ? (
          <ErrorState
            title="Non siamo riusciti a caricare i bandi."
            message={apiErrorMessage(error)}
            onRetry={() => refetch()}
          />
        ) : data && data.items.length === 0 ? (
          <EmptyState
            title="Nessun bando trovato."
            description="Prova a togliere qualche filtro o a usare parole chiave diverse."
            icon={SearchX}
            area="bandi"
            action={
              activeCount > 0 ? (
                <Button type="button" variant="secondary" onClick={azzera}>
                  Azzera i filtri
                </Button>
              ) : undefined
            }
          />
        ) : (
          <>
            {data && (
              <ConteggioRisultati
                totale={data.total}
                conFiltri={activeCount > 0}
                attenuato={isPlaceholderData}
              />
            )}
            <ul
              className={cn(
                "flex flex-col gap-3",
                isPlaceholderData && "opacity-60 transition-opacity",
              )}
            >
              {data?.items.map((bando) => (
                <BandoRow key={bando.id} bando={bando} />
              ))}
            </ul>
            <div className="mt-2">
              <Pagination
                page={filters.page}
                totalPages={data?.total_pages ?? 1}
                onChange={(page) => {
                  update({ page }, { keepPage: true });
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
