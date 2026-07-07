import { Search, SlidersHorizontal, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ActiveFilterChips } from "../components/bandi/ActiveFilterChips";
import { BandiPerTeButton } from "../components/bandi/BandiPerTeButton";
import { SavableBandoCard } from "../components/bandi/SavableBandoCard";
import { FilterSidebar } from "../components/bandi/FilterSidebar";
import { Button } from "../components/ui/Button";
import { Pagination } from "../components/ui/Pagination";
import { BandoCardSkeleton, EmptyState, ErrorState } from "../components/ui/states";
import { useBandi } from "../hooks/useBandi";
import { useBandiFilters } from "../hooks/useBandiFilters";
import { useDebounce } from "../hooks/useDebounce";
import { useLookups } from "../hooks/useLookups";
import { apiErrorMessage } from "../lib/api";

// Il backend mette sempre i bandi chiusi in coda, qualunque ordinamento.
const SORT_LABELS: Record<string, string> = {
  pubblicazione_desc: "Più recenti",
  scadenza_asc: "Scadenza più vicina",
  scadenza_desc: "Scadenza più lontana",
  importo_desc: "Importo più alto",
};

export default function BandiList() {
  const { filters, update, toggleFacet, reset, activeCount } = useBandiFilters();
  const { data: lookups } = useLookups();
  const { data, isPending, isError, error, refetch, isPlaceholderData } = useBandi(filters);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const drawerCloseRef = useRef<HTMLButtonElement>(null);

  // Drawer filtri: chiusura con Esc, focus iniziale sul pulsante di chiusura,
  // blocco dello scroll di sfondo mentre è aperto.
  useEffect(() => {
    if (!drawerOpen) return;
    drawerCloseRef.current?.focus();
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDrawerOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [drawerOpen]);

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

  const sidebar = (
    <FilterSidebar
      lookups={lookups}
      filters={filters}
      onToggleFacet={toggleFacet}
      onToggleStato={toggleStato}
      onUpdate={update}
      onReset={() => {
        setSearchInput("");
        reset();
      }}
      activeCount={activeCount}
    />
  );

  return (
    <div>
      <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="font-display text-2xl font-bold tracking-tight text-slate-900">Bandi</h1>
          <p className="mt-1 text-sm text-slate-500">
            {data ? (
              <>
                <span className="tabular font-medium text-slate-700">{data.total}</span> bandi
                trovati
              </>
            ) : (
              "Esplora il catalogo dei bandi attivi"
            )}
          </p>
        </div>
      </div>

      {/* Toolbar */}
      <div className="mt-5 flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="relative flex-1">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400"
            aria-hidden
          />
          <input
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Cerca per parola chiave, es. “digitalizzazione PMI”…"
            aria-label="Cerca bandi"
            className="h-11 w-full rounded-xl border border-slate-300 bg-white pl-10 pr-4 text-sm shadow-card transition-colors placeholder:text-slate-400 focus:border-brand-500 focus:outline-2 focus:outline-offset-0 focus:outline-brand-500/25"
          />
        </div>
        <div className="flex items-center gap-3">
          <BandiPerTeButton />
          <label className="sr-only" htmlFor="sort-select">
            Ordina per
          </label>
          <select
            id="sort-select"
            value={filters.sort}
            onChange={(e) => update({ sort: e.target.value })}
            className="h-11 cursor-pointer rounded-xl border border-slate-300 bg-white px-3 text-sm shadow-card focus:border-brand-500 focus:outline-none"
          >
            {Object.entries(SORT_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <Button
            variant="secondary"
            className="h-11 lg:hidden"
            onClick={() => setDrawerOpen(true)}
            aria-expanded={drawerOpen}
          >
            <SlidersHorizontal className="size-4" aria-hidden />
            Filtri
            {activeCount > 0 && (
              <span className="rounded-full bg-brand-500 px-1.5 py-0.5 text-xs font-semibold text-white">
                {activeCount}
              </span>
            )}
          </Button>
        </div>
      </div>

      {/* Chips filtri attivi */}
      <div className="mt-3">
        <ActiveFilterChips
          filters={filters}
          lookups={lookups}
          onToggleFacet={toggleFacet}
          onToggleStato={toggleStato}
          onUpdate={update}
          onReset={() => {
            setSearchInput("");
            reset();
          }}
        />
      </div>

      <div className="mt-5 grid gap-6 lg:grid-cols-[280px_1fr]">
        {/* Sidebar desktop */}
        <aside className="hidden lg:block">
          <div className="sticky top-20">{sidebar}</div>
        </aside>

        {/* Drawer mobile */}
        {drawerOpen && (
          <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="Filtri">
            <button
              type="button"
              className="absolute inset-0 bg-brand-950/50"
              onClick={() => setDrawerOpen(false)}
              aria-label="Chiudi filtri"
            />
            <div className="absolute inset-y-0 left-0 w-[85%] max-w-sm overflow-y-auto bg-surface p-4">
              <div className="mb-3 flex items-center justify-between">
                <span className="font-display text-base font-semibold">Filtri</span>
                <button
                  ref={drawerCloseRef}
                  type="button"
                  onClick={() => setDrawerOpen(false)}
                  aria-label="Chiudi"
                  className="cursor-pointer rounded-md p-1.5 text-slate-500 hover:bg-slate-200 focus-visible:outline-2 focus-visible:outline-brand-500"
                >
                  <X className="size-5" aria-hidden />
                </button>
              </div>
              {sidebar}
              <Button className="mt-4 w-full" onClick={() => setDrawerOpen(false)}>
                {data ? `Mostra ${data.total} risultati` : "Mostra risultati"}
              </Button>
            </div>
          </div>
        )}

        {/* Risultati */}
        <section aria-label="Risultati" aria-busy={isPending || isPlaceholderData}>
          {isPending ? (
            <div className="grid gap-4 xl:grid-cols-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <BandoCardSkeleton key={i} />
              ))}
            </div>
          ) : isError ? (
            <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
          ) : data && data.items.length === 0 ? (
            <EmptyState
              title="Nessun bando trovato"
              description="Prova a rimuovere qualche filtro o a usare parole chiave diverse."
              action={
                activeCount > 0 ? (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setSearchInput("");
                      reset();
                    }}
                  >
                    Azzera i filtri
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <>
              <div
                className={
                  "grid gap-4 xl:grid-cols-2" + (isPlaceholderData ? " opacity-60 transition-opacity" : "")
                }
              >
                {data?.items.map((bando) => (
                  <SavableBandoCard key={bando.id} bando={bando} />
                ))}
              </div>
              <div className="mt-8">
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
      </div>
    </div>
  );
}
