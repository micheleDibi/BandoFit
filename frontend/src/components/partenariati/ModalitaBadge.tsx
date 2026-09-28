import type { ComponentProps } from "react";
import { PARTENARIATO_COPY } from "../../lib/copy";
import type { ModalitaPartenariato } from "../../types";
import { Badge } from "../ui/Badge";

const TONI: Record<ModalitaPartenariato, ComponentProps<typeof Badge>["tone"]> = {
  obbligatorio: "brand",
  ammesso: "emerald",
  non_ammesso: "slate",
  non_determinabile: "amber",
};

/** Modalità di partecipazione del bando, sempre in parole (il colore da solo
 *  non basta). Un valore sconosciuto degrada a badge neutro. */
export function ModalitaBadge({ modalita }: { modalita: ModalitaPartenariato }) {
  return (
    <Badge tone={TONI[modalita] ?? "slate"}>
      {PARTENARIATO_COPY.modalita[modalita] ?? PARTENARIATO_COPY.modalita.non_determinabile}
    </Badge>
  );
}
