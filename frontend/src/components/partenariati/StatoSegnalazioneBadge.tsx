import { MODERAZIONE_COPY } from "../../lib/copy";
import type { StatoSegnalazione } from "../../types";
import { Status, type TonoStatus } from "../ui/Status";

const TONI_STATO: Record<StatoSegnalazione, TonoStatus> = {
  ricevuta: "neutro",
  in_esame: "in-apertura",
  decisa: "chiuso",
  ricorso_presentato: "in-apertura",
  ricorso_deciso: "chiuso",
};

/** Stato di una segnalazione (DSA), in parole con il punto di `Status`, mai
 *  il solo colore. Lo usa anche l'admin. */
export function StatoSegnalazioneBadge({ stato }: { stato: StatoSegnalazione }) {
  return (
    <Status tono={TONI_STATO[stato] ?? "neutro"}>
      <span className="sr-only">Stato: </span>
      {MODERAZIONE_COPY.stati[stato] ?? stato}
    </Status>
  );
}

/** Codice breve della segnalazione (lo stesso della notifica di ricezione). */
export const codiceSegnalazione = (id: string) => id.slice(0, 8);
