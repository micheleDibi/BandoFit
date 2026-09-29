import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiErrorCode } from "../lib/api";
import type { AvviaBozzaInput, BozzaDocumento, BozzeCall } from "../types";
import { useActiveCompany } from "./useActiveCompany";
import { useAuth } from "./useAuth";
import { JOB_FINESTRA_POLLING_MS } from "./useCallPartenariato";
import { useFunzioni } from "./useFunzioni";

/* Bozze AI dei documenti del partenariato (WP10): lettera d'intenti, NDA e
 * term sheet per l'azienda attiva che partecipa alla call. La chiave sta
 * sotto la radice della call (`["partenariati", "call", id, …]`), così
 * un'invalidazione della call copre anche le bozze; l'azienda attiva entra
 * nella chiave perché le bozze sono dell'azienda che guarda. La preparazione
 * è un job asincrono (202): si interroga la lista solo mentre una bozza è in
 * preparazione; il server chiude da solo, alla lettura, una bozza rimasta
 * appesa oltre i 10 minuti. */

const BOZZE_POLLING_MS = 3_000;

/** La POST risponde subito (202, il lavoro è in background): il timeout
 *  evita solo un'attesa senza fine se la rete cade. */
const AVVIO_BOZZA_TIMEOUT_MS = 30_000;

export const bozzeKey = (id: string | undefined, aziendaId: string | null) =>
  ["partenariati", "call", id, "bozze", aziendaId] as const;

/** Una bozza è in preparazione ed è partita da meno della finestra di
 *  polling (la stessa dei job AI della call). */
export function bozzaInCorsoRecente(bozza: BozzaDocumento | undefined, ora = Date.now()) {
  if (!bozza || bozza.stato !== "pending") return false;
  const avvio = new Date(bozza.avviata_at).getTime();
  return Number.isFinite(avvio) && ora - avvio < JOB_FINESTRA_POLLING_MS;
}

/** Il 404 (modulo spento, call a cui l'azienda non partecipa) e il 409
 *  dell'azienda mancante non cambiano riprovando. */
const retry = (tentativi: number, errore: unknown) => {
  const codice = apiErrorCode(errore);
  return codice !== "not_found" && codice !== "azienda_mancante" && tentativi < 2;
};

/** Le bozze dell'azienda attiva sulla call (con il testo di quelle pronte),
 *  dalla più recente. Polling solo mentre una è in preparazione. */
export function useBozzePartenariato(id: string | undefined, abilitato = true) {
  const { session } = useAuth();
  const { partenariatiAttivo } = useFunzioni();
  const { activeCompanyId } = useActiveCompany();
  return useQuery({
    queryKey: bozzeKey(id, activeCompanyId),
    queryFn: async () => (await api.get<BozzeCall>(`/partenariati/call/${id}/bozze`)).data,
    enabled: !!session && partenariatiAttivo && !!id && abilitato,
    staleTime: 15_000,
    retry,
    refetchInterval: (query) =>
      (query.state.data?.bozze ?? []).some((b) => bozzaInCorsoRecente(b)) ? BOZZE_POLLING_MS : false,
  });
}

/** URL del PDF di una bozza pronta (GET autenticato, via `downloadFile`). */
export const pdfBozzaUrl = (id: string, bozzaId: string) =>
  `/partenariati/call/${id}/bozze/${bozzaId}/pdf`;

/** Nome del file salvato, lo stesso del server (`nome_file`): solo tipo e
 *  giorno di conclusione a Roma, `bozza-<tipo>-<AAAA-MM-GG>.pdf`. */
export function nomeFileBozza(bozza: BozzaDocumento) {
  const tipo = bozza.tipo.replace(/_/g, "-");
  const istante = new Date(bozza.conclusa_at ?? bozza.avviata_at);
  if (!Number.isFinite(istante.getTime())) return `bozza-${tipo}.pdf`;
  const giorno = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Rome" }).format(istante);
  return `bozza-${tipo}-${giorno}.pdf`;
}

/** La risposta dell'avvio è la bozza in preparazione? */
function isBozza(dati: unknown): dati is BozzaDocumento {
  return (
    !!dati &&
    typeof dati === "object" &&
    typeof (dati as BozzaDocumento).id === "string" &&
    typeof (dati as BozzaDocumento).stato === "string"
  );
}

interface Avvio {
  azienda: string | null;
}

/** Avvio di una bozza (solo il titolare): 202 con la bozza in preparazione,
 *  che entra subito nella lista; il polling fa il resto. Consuma una bozza
 *  del mese: si rileggono le quote. La scrittura si attribuisce all'azienda
 *  della RICHIESTA, fissata in onMutate. */
export function useAvviaBozza(id: string | undefined) {
  const queryClient = useQueryClient();
  const { activeCompanyId } = useActiveCompany();
  return useMutation({
    onMutate: (): Avvio => ({ azienda: activeCompanyId }),
    mutationFn: async (dati: AvviaBozzaInput) =>
      (
        await api.post<BozzaDocumento>(`/partenariati/call/${id}/bozze`, dati, {
          timeout: AVVIO_BOZZA_TIMEOUT_MS,
        })
      ).data,
    onSuccess: (bozza, _variabili, avvio) => {
      const chiave = bozzeKey(id, avvio?.azienda ?? null);
      if (isBozza(bozza)) {
        queryClient.setQueryData<BozzeCall>(chiave, (prima) =>
          prima ? { ...prima, bozze: [bozza, ...prima.bozze.filter((b) => b.id !== bozza.id)] } : prima,
        );
      }
      queryClient.invalidateQueries({ queryKey: chiave, exact: true });
      queryClient.invalidateQueries({ queryKey: ["entitlements"] });
    },
    // Un rifiuto (bozza già in preparazione, limite del mese, budget di oggi)
    // può voler dire che lo stato è cambiato altrove: si rilegge.
    onError: (_errore, _variabili, avvio) => {
      queryClient.invalidateQueries({ queryKey: bozzeKey(id, avvio?.azienda ?? null), exact: true });
      queryClient.invalidateQueries({ queryKey: ["entitlements"] });
    },
  });
}
