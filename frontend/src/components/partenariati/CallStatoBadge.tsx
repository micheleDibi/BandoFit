import { CALL_COPY } from "../../lib/copy";
import type { StatoCall } from "../../types";
import { Status, type TonoStatus } from "../ui/Status";

const TONI: Record<StatoCall, TonoStatus> = {
  bozza: "neutro",
  pubblicata: "aperto",
  chiusa_completata: "chiuso",
  chiusa_annullata: "chiuso",
  scaduta: "chiuso",
  sospesa_moderazione: "attenzione",
};

/** Stato della call, sempre in parole con il punto di `Status` (il colore da
 *  solo non basta). Stesse props di prima: lo usano anche il wizard e l'admin. */
export function CallStatoBadge({ stato, className }: { stato: StatoCall; className?: string }) {
  return (
    <Status tono={TONI[stato] ?? "neutro"} className={className}>
      <span className="sr-only">Stato: </span>
      {CALL_COPY.stati[stato] ?? stato}
    </Status>
  );
}
