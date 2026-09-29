import type { ComponentProps } from "react";
import { CALL_COPY } from "../../lib/copy";
import type { StatoCall } from "../../types";
import { Badge } from "../ui/Badge";

const TONI: Record<StatoCall, ComponentProps<typeof Badge>["tone"]> = {
  bozza: "amber",
  pubblicata: "emerald",
  chiusa_completata: "brand",
  chiusa_annullata: "slate",
  scaduta: "slate",
  sospesa_moderazione: "red",
};

/** Stato della call, sempre in parole (il colore da solo non basta). */
export function CallStatoBadge({ stato, className }: { stato: StatoCall; className?: string }) {
  return (
    <Badge tone={TONI[stato] ?? "slate"} className={className}>
      <span className="sr-only">Stato: </span>
      {CALL_COPY.stati[stato] ?? stato}
    </Badge>
  );
}
