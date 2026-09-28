import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { NOTIFICHE_COPY } from "../lib/copy";
import type { CompanySummary } from "../types";
import { useActiveCompany } from "./useActiveCompany";
import { useMe } from "./useMe";

/** Parametro dei link delle notifiche che riguardano un'azienda precisa. */
export const PARAMETRO_AZIENDA = "azienda";

export type DecisioneAziendaDaLink =
  /** Mancano ancora i dati per decidere (profilo o elenco aziende). */
  | { tipo: "attendi" }
  /** Niente da cambiare: si toglie solo il parametro. */
  | { tipo: "rimuovi" }
  /** Si passa all'azienda del link, poi si toglie il parametro. */
  | { tipo: "attiva"; id: string }
  /** L'azienda del link non è tra quelle dell'utente. */
  | { tipo: "non_gestita" };

/** Decisione pura sul parametro `?azienda=<id>`.
 *  - Si aspetta l'elenco delle aziende dell'utente (senza elenco non si
 *    verifica nulla: si resta sull'azienda attiva). Un'azienda che non c'è →
 *    avviso.
 *  - Chi non gestisce più aziende ne vede una sola, quella di default
 *    (`attiva`): se il link è per un'altra (es. un ex Advisor tornato a una
 *    sola azienda) non la si può aprire → avviso, altrimenti si toglie solo
 *    il parametro.
 *  - Advisor: si aspetta anche che l'azienda attiva sia stata risolta (così il
 *    cambio non si sovrappone a quello iniziale di `ActiveCompanyProvider`);
 *    poi, se l'azienda del link è diversa dall'attiva, si attiva. */
export function decidiAziendaDaLink(input: {
  idLink: string;
  profiloCaricato: boolean;
  isMulti: boolean;
  elenco: "caricamento" | "errore" | "pronto";
  companies: readonly CompanySummary[];
  activeCompanyId: string | null;
}): DecisioneAziendaDaLink {
  const { idLink, profiloCaricato, isMulti, elenco, companies, activeCompanyId } = input;
  if (!profiloCaricato) return { tipo: "attendi" };
  // Senza elenco non si può verificare nulla: si resta sull'azienda attiva.
  if (elenco === "errore") return { tipo: "rimuovi" };
  if (elenco === "caricamento") return { tipo: "attendi" };
  const accessibile = (id: string | null) => !!id && companies.some((c) => c.id === id);
  if (!accessibile(idLink)) return { tipo: "non_gestita" };
  if (!isMulti) {
    const predefinita = companies.find((c) => c.attiva)?.id ?? null;
    return idLink === predefinita ? { tipo: "rimuovi" } : { tipo: "non_gestita" };
  }
  if (!accessibile(activeCompanyId)) return { tipo: "attendi" };
  if (idLink === activeCompanyId) return { tipo: "rimuovi" };
  return { tipo: "attiva", id: idLink };
}

/** Deep link delle notifiche per gli Advisor: legge `?azienda=<id>`, rende
 *  attiva quell'azienda se l'utente la gestisce e toglie il parametro
 *  dall'URL (replace, l'hash come `#bilanci` resta). Restituisce l'avviso da
 *  mostrare quando l'azienda non è tra le sue, altrimenti null. */
export function useAziendaDaLink(): { avviso: string | null } {
  const location = useLocation();
  const navigate = useNavigate();
  const { data: me } = useMe();
  const { activeCompanyId, isMulti, setActiveCompany } = useActiveCompany();
  const [nonGestita, setNonGestita] = useState(false);

  const idLink = new URLSearchParams(location.search).get(PARAMETRO_AZIENDA);
  // Stessa query (e stessa cache) di `useVisibleCompanies`, che però parte
  // solo per gli Advisor: qui serve anche a chi ha una sola azienda, e solo
  // quando c'è un link da verificare.
  const elencoQuery = useQuery({
    queryKey: ["companies", "visibili"],
    queryFn: async () => (await api.get<CompanySummary[]>("/me/aziende/visibili")).data,
    enabled: me !== undefined && (isMulti || !!idLink),
    staleTime: 60_000,
  });
  const companies = elencoQuery.data ?? [];
  const elenco = elencoQuery.isError ? "errore" : elencoQuery.isSuccess ? "pronto" : "caricamento";

  useEffect(() => {
    if (!idLink) return;
    const decisione = decidiAziendaDaLink({
      idLink,
      profiloCaricato: me !== undefined,
      isMulti,
      elenco,
      companies,
      activeCompanyId,
    });
    if (decisione.tipo === "attendi") return;
    setNonGestita(decisione.tipo === "non_gestita");
    if (decisione.tipo === "attiva") setActiveCompany(decisione.id);
    const parametri = new URLSearchParams(location.search);
    parametri.delete(PARAMETRO_AZIENDA);
    const search = parametri.toString();
    navigate(
      { pathname: location.pathname, search: search ? `?${search}` : "", hash: location.hash },
      { replace: true },
    );
  }, [
    idLink,
    me,
    isMulti,
    elenco,
    companies,
    activeCompanyId,
    setActiveCompany,
    navigate,
    location.pathname,
    location.search,
    location.hash,
  ]);

  return { avviso: nonGestita ? NOTIFICHE_COPY.aziendaNonGestita : null };
}
