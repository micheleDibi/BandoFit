import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { BilanciOut } from "../types";
import { useActiveCompany } from "./useActiveCompany";
import { useAuth } from "./useAuth";

/** Il server chiude il recupero entro 55 s (BILANCI_DEADLINE_SECONDS): il
 *  client aspetta qualcosa in più, così non abbandona una risposta in arrivo. */
const RECUPERA_TIMEOUT_MS = 60_000;

/** Query key per azienda: la risposta di un recupero (fino a un minuto) può
 *  arrivare quando un Advisor è già passato a un'altra azienda, e va scritta
 *  sotto l'azienda della RICHIESTA — mai sotto quella attiva in quel momento.
 *  Le invalidazioni con il solo prefisso `["company-bilanci"]` le coprono
 *  tutte. */
const bilanciKey = (aziendaId: string | null) => ["company-bilanci", aziendaId] as const;

/** Bilanci per esercizio dell'azienda attiva, con indicatori e fasce già
 *  calcolati dal server. Leggerli è gratuito (anche per i membri, in sola
 *  lettura). */
export function useBilanci() {
  const { session } = useAuth();
  const { activeCompanyId } = useActiveCompany();
  return useQuery({
    queryKey: bilanciKey(activeCompanyId),
    queryFn: async () => (await api.get<BilanciOut>("/me/company/bilanci")).data,
    enabled: !!session,
    staleTime: 60_000,
  });
}

/** «Recupera i bilanci»: una chiamata al Registro Imprese pagata dalla
 *  piattaforma (solo il titolare). Nessun retry: la mutation non ne fa e il
 *  server impone comunque un'attesa tra due tentativi. */
export function useRecuperaBilanci() {
  const queryClient = useQueryClient();
  // La chiamata dura fino a un minuto e un Advisor può cambiare azienda nel
  // frattempo (la sezione viene smontata, ma questo onSuccess scatta lo
  // stesso): l'azienda della richiesta si fissa in onMutate e la risposta si
  // scrive sotto la SUA chiave, che un'altra azienda non legge mai.
  const { activeCompanyId } = useActiveCompany();

  return useMutation({
    onMutate: () => ({ azienda: activeCompanyId }),
    mutationFn: async () =>
      (
        await api.post<BilanciOut>("/me/company/bilanci/recupera", null, {
          timeout: RECUPERA_TIMEOUT_MS,
        })
      ).data,
    onSuccess: (data, _variabili, avvio) => {
      if (!avvio) return;
      queryClient.setQueryData(bilanciKey(avvio.azienda), data);
      // Il dossier (Dati economici) e i facet dipendono dagli stessi dati.
      queryClient.invalidateQueries({ queryKey: ["company-dossier"] });
      queryClient.invalidateQueries({ queryKey: ["company-facets"] });
    },
    // Anche un tentativo fallito cambia lo stato (esito, attesa prima di
    // riprovare): rileggiamo, così il bottone mostra da quando si può riprovare.
    onError: () => {
      queryClient.invalidateQueries({ queryKey: ["company-bilanci"] });
    },
  });
}
