import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api } from "../lib/api";
import { chiusureRichieste, intervalloPollingBilanci } from "../lib/bilanci";
import type { BilanciUfficiali, BilancioRichiesta, StatoRichiestaBilancio } from "../types";
import { useActiveCompany } from "./useActiveCompany";
import { useAuth } from "./useAuth";
import { useFunzioni } from "./useFunzioni";

/** La POST risponde di solito in pochi secondi, ma nel caso peggiore somma
 *  il controllo della forma giuridica, il token e l'invio al Registro
 *  Imprese. Anche se il client smette di aspettare la richiesta può essere
 *  partita: dopo ogni errore la lista si rilegge, e una seconda richiesta
 *  sulla stessa azienda riceve `bilancio_in_corso` (nessun doppio consumo). */
const RICHIESTA_TIMEOUT_MS = 90_000;

/** Query key per azienda, come `useBilanci`: la risposta della POST va
 *  scritta sotto l'azienda della RICHIESTA, anche se un Advisor nel
 *  frattempo ne ha scelta un'altra. Le invalidazioni col solo prefisso
 *  `["company-bilanci-ufficiali"]` le coprono tutte. */
const bilanciUfficialiKey = (aziendaId: string | null) =>
  ["company-bilanci-ufficiali", aziendaId] as const;

/** Bilanci ufficiali dell'azienda attiva: addon, unità del titolare, anni già
 *  acquisiti e storico delle richieste. Leggere la lista fa anche avanzare le
 *  richieste aperte sul server. Polling ogni 30 s finché c'è una richiesta
 *  aperta nata da meno di 45 minuti. Quando una richiesta si conclude
 *  rilegge l'inventario addon (può esserci un rimborso) e, se è completata,
 *  i bilanci per esercizio. A storico spento non chiama nulla (la rotta
 *  risponderebbe 404). */
export function useBilanciUfficiali() {
  const { session } = useAuth();
  const { activeCompanyId } = useActiveCompany();
  const { bilanciStoricoAttivo } = useFunzioni();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: bilanciUfficialiKey(activeCompanyId),
    queryFn: async () =>
      (await api.get<BilanciUfficiali>("/me/company/bilanci/ufficiale")).data,
    enabled: !!session && bilanciStoricoAttivo,
    staleTime: 15_000,
    refetchInterval: (q) => intervalloPollingBilanci(q.state.data?.richieste),
  });

  // Stati visti alla lettura precedente, per riconoscere le chiusure. Legati
  // all'azienda: dopo un cambio azienda la prima lettura non è un confronto.
  const precedenti = useRef<{
    azienda: string | null;
    stati: Map<string, StatoRichiestaBilancio>;
  } | null>(null);
  const richieste = query.data?.richieste;

  useEffect(() => {
    if (!richieste) return;
    const prima =
      precedenti.current && precedenti.current.azienda === activeCompanyId
        ? precedenti.current.stati
        : null;
    precedenti.current = {
      azienda: activeCompanyId,
      stati: new Map(richieste.map((r) => [r.id, r.stato])),
    };
    const { completate, chiuse } = chiusureRichieste(prima, richieste);
    if (completate) {
      // Il bilancio ufficiale entra nella tabella (fonte xbrl) e nei facet.
      queryClient.invalidateQueries({ queryKey: ["company-bilanci"] });
      queryClient.invalidateQueries({ queryKey: ["company-facets"] });
    }
    if (chiuse) queryClient.invalidateQueries({ queryKey: ["my-addons"] });
  }, [richieste, activeCompanyId, queryClient]);

  return query;
}

/** «Richiedi il bilancio ufficiale» (solo il titolare): consuma SEMPRE
 *  un'unità dell'addon. Nessun retry: la mutation non ne fa. */
export function useRichiediBilancioUfficiale() {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();

  return useMutation({
    onMutate: () => ({ azienda: activeCompanyId }),
    mutationFn: async (anno: number | null) =>
      (
        await api.post<BilancioRichiesta>(
          "/me/company/bilanci/ufficiale",
          { anno },
          { timeout: RICHIESTA_TIMEOUT_MS },
        )
      ).data,
    onSuccess: (richiesta, _anno, avvio) => {
      if (avvio) {
        queryClient.setQueryData<BilanciUfficiali>(
          bilanciUfficialiKey(avvio.azienda),
          (prima) =>
            prima && {
              ...prima,
              richieste: [richiesta, ...prima.richieste.filter((r) => r.id !== richiesta.id)],
            },
        );
      }
      // Unità e storico veri (consumo, eventuale rimborso immediato) li dice
      // il server.
      queryClient.invalidateQueries({ queryKey: ["my-addons"] });
      queryClient.invalidateQueries({ queryKey: ["company-bilanci-ufficiali"] });
    },
    // Anche un tentativo fallito può aver consumato un'unità o creato una
    // richiesta (timeout, errore del fornitore): si rilegge tutto.
    onError: () => {
      queryClient.invalidateQueries({ queryKey: ["my-addons"] });
      queryClient.invalidateQueries({ queryKey: ["company-bilanci-ufficiali"] });
    },
  });
}
