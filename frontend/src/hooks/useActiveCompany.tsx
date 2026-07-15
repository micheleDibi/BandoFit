import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { setActiveCompanyHeader } from "../lib/api";
import type { CompanySummary } from "../types";
import { useCompanies } from "./useCompanies";
import { useMe } from "./useMe";

const STORAGE_KEY = "bandofit.activeCompany";

interface ActiveCompanyValue {
  /** id dell'azienda attiva (Advisor), null per i non-Advisor. */
  activeCompanyId: string | null;
  /** true = piano multi-azienda: mostra lo switcher e la pagina Aziende. */
  isMulti: boolean;
  companies: CompanySummary[];
  /** Cambia azienda: persiste, aggiorna l'header e SVUOTA la cache query
   *  (garanzia di segregazione: nessun dato dell'altra azienda sopravvive). */
  setActiveCompany: (id: string) => void;
}

const ActiveCompanyContext = createContext<ActiveCompanyValue>({
  activeCompanyId: null,
  isMulti: false,
  companies: [],
  setActiveCompany: () => {},
});

export function ActiveCompanyProvider({ children }: { children: ReactNode }) {
  const { data: me } = useMe();
  const isMulti = (me?.max_aziende ?? 1) > 1;
  const companiesQuery = useCompanies();
  const companies = companiesQuery.data?.aziende ?? [];
  const queryClient = useQueryClient();
  const [activeCompanyId, setActiveCompanyId] = useState<string | null>(() =>
    localStorage.getItem(STORAGE_KEY),
  );
  // La prima risoluzione può richiedere un rifetch (le query iniziali erano
  // partite senza header): lo facciamo una sola volta.
  const reconciled = useRef(false);

  // Non-Advisor (una volta noto il piano): nessun header, e si ripulisce la
  // chiave persistita — utile a un ex-Advisor retrocesso. Finché `me` è in
  // caricamento non si tocca nulla, per non perdere la scelta al reload.
  useEffect(() => {
    if (me === undefined || isMulti) return;
    setActiveCompanyHeader(null);
    reconciled.current = false;
    if (activeCompanyId !== null) {
      localStorage.removeItem(STORAGE_KEY);
      setActiveCompanyId(null);
    }
  }, [me, isMulti, activeCompanyId]);

  // Advisor: risolve l'azienda attiva contro l'elenco. Una scelta stantìa o di
  // un'azienda cancellata/archiviata torna al default (la più vecchia viva).
  useEffect(() => {
    if (!isMulti || !companiesQuery.isSuccess) return;
    const defaultId = companies.find((c) => c.attiva)?.id ?? companies[0]?.id ?? null;
    const valid = activeCompanyId && companies.some((c) => c.id === activeCompanyId);
    const resolved = valid ? activeCompanyId : defaultId;

    setActiveCompanyHeader(resolved);
    if (resolved) localStorage.setItem(STORAGE_KEY, resolved);
    else localStorage.removeItem(STORAGE_KEY);
    if (resolved !== activeCompanyId) setActiveCompanyId(resolved);

    // Le prime query non avevano header → hanno letto il default del backend
    // (la più vecchia): se l'azienda risolta è un'altra, servono di nuovo.
    if (!reconciled.current) {
      reconciled.current = true;
      if (resolved && resolved !== defaultId) queryClient.clear();
    }
  }, [isMulti, companiesQuery.isSuccess, companies, activeCompanyId, queryClient]);

  const setActiveCompany = useCallback(
    (id: string) => {
      setActiveCompanyId((prev) => {
        if (prev === id) return prev;
        localStorage.setItem(STORAGE_KEY, id);
        setActiveCompanyHeader(id);
        queryClient.clear();
        return id;
      });
    },
    [queryClient],
  );

  return (
    <ActiveCompanyContext.Provider
      value={{ activeCompanyId, isMulti, companies, setActiveCompany }}
    >
      {children}
    </ActiveCompanyContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useActiveCompany(): ActiveCompanyValue {
  return useContext(ActiveCompanyContext);
}
