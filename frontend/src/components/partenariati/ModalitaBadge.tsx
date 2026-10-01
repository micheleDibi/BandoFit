import { PARTENARIATO_COPY } from "../../lib/copy";
import type { ModalitaPartenariato } from "../../types";
import { Status, type TonoStatus } from "../ui/Status";

const TONI: Record<ModalitaPartenariato, TonoStatus> = {
  obbligatorio: "aperto",
  ammesso: "aperto",
  non_ammesso: "chiuso",
  non_determinabile: "in-apertura",
};

/** Modalità di partecipazione del bando, sempre in parole con il punto di
 *  `Status` (il colore da solo non basta). Un valore sconosciuto degrada a
 *  neutro. Stesse props di prima: lo usano anche la scheda del bando e il wizard. */
export function ModalitaBadge({ modalita }: { modalita: ModalitaPartenariato }) {
  return (
    <Status tono={TONI[modalita] ?? "neutro"}>
      {PARTENARIATO_COPY.modalita[modalita] ?? PARTENARIATO_COPY.modalita.non_determinabile}
    </Status>
  );
}
