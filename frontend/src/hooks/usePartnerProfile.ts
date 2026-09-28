import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api, apiErrorCode } from "../lib/api";
import type {
  ConsensoPartnerInput,
  InformativaPartner,
  PartnerProfile,
  PartnerProfileInput,
  PartnerPubblico,
  ReferentePartnerInput,
  RispostaReferenteInput,
} from "../types";
import { useActiveCompany } from "./useActiveCompany";
import { useAuth } from "./useAuth";
import { useFunzioni } from "./useFunzioni";

/** Polling della bozza AI: ogni 3 s finché è in corso, ma solo nei primi 5
 *  minuti. Oltre, il server la chiude da solo alla lettura successiva (e un
 *  refetch al focus della finestra basta a vederlo). */
const BOZZA_POLLING_MS = 3_000;
export const BOZZA_FINESTRA_POLLING_MS = 5 * 60_000;

/** La POST della bozza risponde subito (202, il lavoro è in background):
 *  il timeout evita solo un'attesa senza fine se la rete cade. */
const AVVIO_BOZZA_TIMEOUT_MS = 30_000;

/** Radice unica `["partenariati", …]`. Il profilo è dell'AZIENDA: la chiave
 *  contiene l'azienda attiva, e le risposte si scrivono sotto l'azienda della
 *  RICHIESTA (fissata in onMutate), mai sotto quella attiva in quel momento. */
export const partnerProfileKey = (aziendaId: string | null) =>
  ["partenariati", "profilo", aziendaId] as const;
export const partnerAnteprimaKey = (aziendaId: string | null) =>
  ["partenariati", "profilo", aziendaId, "anteprima"] as const;
/** Prefisso di tutti i profili (e delle anteprime), per le invalidazioni. */
export const PARTNER_PROFILE_ROOT = ["partenariati", "profilo"] as const;
const INFORMATIVA_KEY = ["partenariati", "informativa"] as const;

/** La bozza AI sta lavorando ed è partita da meno della finestra di polling. */
export function bozzaInCorsoRecente(profilo: PartnerProfile | undefined, ora = Date.now()) {
  const bozza = profilo?.bozza_ai;
  if (!bozza || bozza.stato !== "in_corso" || !bozza.avviata_at) return false;
  const avvio = new Date(bozza.avviata_at).getTime();
  return Number.isFinite(avvio) && ora - avvio < BOZZA_FINESTRA_POLLING_MS;
}

interface Avvio {
  azienda: string | null;
}

/** Dopo una scrittura riuscita: profilo aggiornato con la risposta, anteprima
 *  da rileggere (dipende dagli stessi dati). */
function scriviProfilo(queryClient: QueryClient, avvio: Avvio | undefined, dati: PartnerProfile) {
  if (!avvio) return;
  queryClient.setQueryData(partnerProfileKey(avvio.azienda), dati);
  queryClient.invalidateQueries({ queryKey: partnerAnteprimaKey(avvio.azienda) });
}

/** Profilo partner dell'azienda attiva (anche i membri lo leggono, in sola
 *  lettura). A modulo spento la rotta risponde 404: non si chiama proprio.
 *  `abilitato=false` lascia la query ferma (p.es. dialog d'import chiuso). */
export function usePartnerProfile(abilitato = true) {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  const { activeCompanyId } = useActiveCompany();
  return useQuery({
    queryKey: partnerProfileKey(activeCompanyId),
    queryFn: async () => (await api.get<PartnerProfile>("/me/partner-profile")).data,
    enabled: !!session && partenariatiAttivo && abilitato,
    staleTime: 30_000,
    // Il 404 del modulo spento (o dell'azienda assente) non cambia riprovando.
    retry: (tentativi, errore) => apiErrorCode(errore) !== "not_found" && tentativi < 2,
    refetchInterval: (query) =>
      bozzaInCorsoRecente(query.state.data) ? BOZZA_POLLING_MS : false,
  });
}

/** Salva il profilo (PUT: il profilo intero). Solo il titolare. */
export function useSalvaPartnerProfile() {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async (dati: PartnerProfileInput) =>
      (await api.put<PartnerProfile>("/me/partner-profile", dati)).data,
    onSuccess: (dati, _variabili, avvio) => scriviProfilo(queryClient, avvio, dati),
  });
}

/** Concessione, revoca o cambio di anonimato. Se l'informativa è cambiata nel
 *  frattempo (409 `informativa_superata`) si rilegge il testo da mostrare. */
export function useConsensoPartner() {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async (dati: ConsensoPartnerInput) =>
      (await api.post<PartnerProfile>("/me/partner-profile/consenso", dati)).data,
    onSuccess: (dati, _variabili, avvio) => scriviProfilo(queryClient, avvio, dati),
    onError: (errore, _variabili, avvio) => {
      if (apiErrorCode(errore) === "informativa_superata") {
        queryClient.invalidateQueries({ queryKey: INFORMATIVA_KEY });
      }
      // Identità, rappresentanza o sospensione possono essere cambiate: lo
      // stato mostrato va riallineato al server.
      queryClient.invalidateQueries({
        queryKey: partnerProfileKey(avvio?.azienda ?? activeCompanyId),
      });
    },
  });
}

/** Il titolare propone un membro come referente, o torna referente lui. */
export function useReferentePartner() {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async (dati: ReferentePartnerInput) =>
      (await api.post<PartnerProfile>("/me/partner-profile/referente", dati)).data,
    onSuccess: (dati, _variabili, avvio) => scriviProfilo(queryClient, avvio, dati),
    onError: (_errore, _variabili, avvio) => {
      queryClient.invalidateQueries({
        queryKey: partnerProfileKey(avvio?.azienda ?? activeCompanyId),
      });
    },
  });
}

/** Risposta della persona proposta (accetta/rifiuta) o rinuncia del
 *  referente. */
export function useRispostaReferente() {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async (dati: RispostaReferenteInput) =>
      (await api.post<PartnerProfile>("/me/partner-profile/referente/risposta", dati)).data,
    onSuccess: (dati, _variabili, avvio) => scriviProfilo(queryClient, avvio, dati),
    onError: (errore, _variabili, avvio) => {
      if (apiErrorCode(errore) === "informativa_superata") {
        queryClient.invalidateQueries({ queryKey: INFORMATIVA_KEY });
      }
      queryClient.invalidateQueries({
        queryKey: partnerProfileKey(avvio?.azienda ?? activeCompanyId),
      });
    },
  });
}

/** Avvia la bozza AI del profilo (202: lavora in background, il profilo
 *  torna con la bozza `in_corso` e il polling la segue). A carico della
 *  piattaforma, con un limite giornaliero per azienda. */
export function useAvviaBozzaAi() {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async () =>
      (
        await api.post<PartnerProfile>("/me/partner-profile/bozza-ai", null, {
          timeout: AVVIO_BOZZA_TIMEOUT_MS,
        })
      ).data,
    onSuccess: (dati, _variabili, avvio) => {
      if (avvio) queryClient.setQueryData(partnerProfileKey(avvio.azienda), dati);
    },
    // Un rifiuto (bozza già in corso, limite di oggi) può voler dire che lo
    // stato è cambiato altrove: si rilegge.
    onError: (_errore, _variabili, avvio) => {
      queryClient.invalidateQueries({
        queryKey: partnerProfileKey(avvio?.azienda ?? activeCompanyId),
      });
    },
  });
}

/** Scarta la proposta dell'AI (il profilo salvato non cambia). */
export function useScartaBozzaAi() {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async () => (await api.delete<PartnerProfile | null>("/me/partner-profile/bozza-ai")).data,
    onSuccess: (dati, _variabili, avvio) => {
      if (!avvio) return;
      const chiave = partnerProfileKey(avvio.azienda);
      // Se la risposta è il profilo si usa quella; altrimenti (204) si rilegge.
      if (dati && typeof dati === "object" && "profilo" in dati) {
        queryClient.setQueryData(chiave, dati);
      } else {
        queryClient.invalidateQueries({ queryKey: chiave, exact: true });
      }
    },
  });
}

/** «Come ti vedono le altre aziende»: la proiezione pubblica del profilo
 *  SALVATO, anche se non è visibile. */
export function usePartnerAnteprima(abilitata = true) {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  const { activeCompanyId } = useActiveCompany();
  return useQuery({
    queryKey: partnerAnteprimaKey(activeCompanyId),
    queryFn: async () =>
      (await api.get<PartnerPubblico>("/me/partner-profile/anteprima")).data,
    enabled: !!session && partenariatiAttivo && abilitata,
    staleTime: 30_000,
  });
}

/** Informative del modulo (partner e referente): cambiano solo con un
 *  rilascio, quindi restano in cache un'ora. */
export function useInformativaPartner(abilitata = true) {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  return useQuery({
    queryKey: INFORMATIVA_KEY,
    queryFn: async () =>
      (await api.get<InformativaPartner>("/partenariati/informativa")).data,
    enabled: !!session && partenariatiAttivo && abilitata,
    staleTime: 60 * 60_000,
  });
}
