import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

/** Stato dei filtri della lista bandi, serializzato nei searchParams dell'URL
 *  (condivisibile, back-friendly, unica fonte di verità per la query). */
export interface BandiFilterState {
  q: string;
  stato: string[];
  tipologie: number[];
  modalita: number[];
  programmi: number[];
  regioni: number[];
  settori: number[];
  beneficiari: number[];
  ateco: number[];
  importo_min: number | null;
  importo_max: number | null;
  scade_entro_giorni: number | null;
  sort: string;
  page: number;
}

const NUMERIC_FACETS = [
  "tipologie",
  "modalita",
  "programmi",
  "regioni",
  "settori",
  "beneficiari",
  "ateco",
] as const;

/** Faccette con id numerici (tutte tranne `stato`, che usa stringhe). */
export type FacetKey = (typeof NUMERIC_FACETS)[number];

const LIST_KEYS = ["stato", ...NUMERIC_FACETS] as const;

const DEFAULT_SORT = "scadenza_asc";

function parseCsvNumbers(raw: string | null): number[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((x) => Number(x))
    .filter((x) => Number.isInteger(x) && x > 0);
}

function parseCsvStrings(raw: string | null): string[] {
  if (!raw) return [];
  return raw.split(",").filter(Boolean);
}

function parsePositiveInt(raw: string | null): number | null {
  if (!raw) return null;
  const num = Number(raw);
  return Number.isInteger(num) && num >= 0 ? num : null;
}

export function useBandiFilters() {
  const [searchParams, setSearchParams] = useSearchParams();

  const filters: BandiFilterState = useMemo(
    () => ({
      q: searchParams.get("q") ?? "",
      stato: parseCsvStrings(searchParams.get("stato")),
      tipologie: parseCsvNumbers(searchParams.get("tipologie")),
      modalita: parseCsvNumbers(searchParams.get("modalita")),
      programmi: parseCsvNumbers(searchParams.get("programmi")),
      regioni: parseCsvNumbers(searchParams.get("regioni")),
      settori: parseCsvNumbers(searchParams.get("settori")),
      beneficiari: parseCsvNumbers(searchParams.get("beneficiari")),
      ateco: parseCsvNumbers(searchParams.get("ateco")),
      importo_min: parsePositiveInt(searchParams.get("importo_min")),
      importo_max: parsePositiveInt(searchParams.get("importo_max")),
      scade_entro_giorni: parsePositiveInt(searchParams.get("scade_entro_giorni")),
      sort: searchParams.get("sort") ?? DEFAULT_SORT,
      page: Math.max(1, parsePositiveInt(searchParams.get("page")) ?? 1),
    }),
    [searchParams],
  );

  const update = useCallback(
    (changes: Partial<BandiFilterState>, options?: { keepPage?: boolean }) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          const setOrDelete = (key: string, value: string | null) => {
            if (value === null || value === "") next.delete(key);
            else next.set(key, value);
          };
          for (const [key, value] of Object.entries(changes)) {
            if (value === undefined) continue;
            if (Array.isArray(value)) {
              setOrDelete(key, value.length ? value.join(",") : null);
            } else if (key === "sort") {
              setOrDelete(key, value === DEFAULT_SORT ? null : String(value));
            } else if (key === "page") {
              setOrDelete(key, Number(value) > 1 ? String(value) : null);
            } else {
              setOrDelete(key, value === null ? null : String(value));
            }
          }
          // Ogni modifica ai filtri riporta a pagina 1.
          if (!options?.keepPage && changes.page === undefined) next.delete("page");
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const toggleFacet = useCallback(
    (key: FacetKey, id: number) => {
      const current = filters[key];
      const next = current.includes(id)
        ? current.filter((x) => x !== id)
        : [...current, id];
      update({ [key]: next });
    },
    [filters, update],
  );

  const reset = useCallback(() => {
    setSearchParams(new URLSearchParams(), { replace: true });
  }, [setSearchParams]);

  const activeCount = useMemo(() => {
    let count = 0;
    for (const key of LIST_KEYS) count += filters[key].length;
    if (filters.q) count += 1;
    if (filters.importo_min !== null) count += 1;
    if (filters.importo_max !== null) count += 1;
    if (filters.scade_entro_giorni !== null) count += 1;
    return count;
  }, [filters]);

  return { filters, update, toggleFacet, reset, activeCount };
}

/** Converte lo stato filtri nei parametri attesi da GET /bandi. */
export function toApiParams(filters: BandiFilterState): Record<string, string | number> {
  const params: Record<string, string | number> = {
    page: filters.page,
    page_size: 20,
    sort: filters.sort,
  };
  if (filters.q) params.q = filters.q;
  for (const key of LIST_KEYS) {
    const values = filters[key];
    if (values.length) params[key] = values.join(",");
  }
  if (filters.importo_min !== null) params.importo_min = filters.importo_min;
  if (filters.importo_max !== null) params.importo_max = filters.importo_max;
  if (filters.scade_entro_giorni !== null) params.scade_entro_giorni = filters.scade_entro_giorni;
  return params;
}
