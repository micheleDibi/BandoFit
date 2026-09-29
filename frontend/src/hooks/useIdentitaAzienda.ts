import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiErrorCode } from "../lib/api";
import type { VerificaIdentita, VerificaIdentitaInput } from "../types";
import { useActiveCompany } from "./useActiveCompany";
import { useAuth } from "./useAuth";
import { useFunzioni } from "./useFunzioni";
import { PARTNER_PROFILE_ROOT } from "./usePartnerProfile";

/** Verifica dell'identità dell'azienda da parte della piattaforma (WP9),
 *  `GET/POST /me/partner-profile/identita` (la stessa verifica arriva anche
 *  in `identita.verifica` del profilo partner). Radice unica
 *  `["partenariati", …]` con l'azienda attiva nella chiave: la verifica è
 *  dell'AZIENDA, e la risposta di una richiesta si scrive sotto l'azienda
 *  della richiesta (fissata in onMutate), mai sotto quella attiva in quel
 *  momento. */
export const identitaAziendaKey = (aziendaId: string | null) =>
  ["partenariati", "identita", aziendaId] as const;

/** Stato della verifica dell'azienda attiva (i membri lo leggono). A modulo
 *  spento la rotta risponde 404: non si chiama proprio. */
export function useIdentitaAzienda(abilitato = true) {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  const { activeCompanyId } = useActiveCompany();
  return useQuery({
    queryKey: identitaAziendaKey(activeCompanyId),
    queryFn: async () =>
      (await api.get<VerificaIdentita>("/me/partner-profile/identita")).data,
    enabled: !!session && partenariatiAttivo && abilitato,
    staleTime: 30_000,
    // Il 404 (modulo spento, nessuna azienda) non cambia riprovando.
    retry: (tentativi, errore) => apiErrorCode(errore) !== "not_found" && tentativi < 2,
  });
}

/** L'azienda attiva ha l'identità verificata dalla piattaforma OGGI (stato
 *  verificata e dati del registro ancora coerenti): è ciò che sblocca il nome. */
export function identitaVerificata(dati: VerificaIdentita | undefined): boolean {
  return dati?.verificata === true;
}

/** Il titolare chiede la verifica (una richiesta aperta alla volta). */
export function useRichiediIdentita() {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: () => ({ azienda: activeCompanyId }),
    mutationFn: async (dati: VerificaIdentitaInput) =>
      (await api.post<VerificaIdentita>("/me/partner-profile/identita", dati)).data,
    onSuccess: (dati, _variabili, avvio) => {
      queryClient.setQueryData(identitaAziendaKey(avvio?.azienda ?? null), dati);
      // Il profilo partner porta la stessa verifica (`identita.verifica`).
      void queryClient.invalidateQueries({ queryKey: PARTNER_PROFILE_ROOT });
    },
    onError: (_errore, _variabili, avvio) => {
      // Richiesta già aperta, identità già verificata, dati del registro
      // cambiati: lo stato mostrato va riallineato al server.
      void queryClient.invalidateQueries({
        queryKey: identitaAziendaKey(avvio?.azienda ?? activeCompanyId),
      });
    },
  });
}
