import { CheckCircle2, Clock } from "lucide-react";
import { MODERAZIONE_COPY } from "../../lib/copy";
import type { StatoSegnalazione } from "../../types";
import { Badge, type BadgeProps } from "../ui/Badge";

const TONI_STATO: Record<StatoSegnalazione, BadgeProps["tone"]> = {
  ricevuta: "slate",
  in_esame: "amber",
  decisa: "brand",
  ricorso_presentato: "amber",
  ricorso_deciso: "brand",
};

/** Stato di una segnalazione (DSA): icona E testo, mai il solo colore. */
export function StatoSegnalazioneBadge({ stato }: { stato: StatoSegnalazione }) {
  const decisa = stato === "decisa" || stato === "ricorso_deciso";
  const Icona = decisa ? CheckCircle2 : Clock;
  return (
    <Badge tone={TONI_STATO[stato] ?? "slate"}>
      <Icona className="size-3.5" aria-hidden />
      {MODERAZIONE_COPY.stati[stato] ?? stato}
    </Badge>
  );
}

/** Codice breve della segnalazione (lo stesso della notifica di ricezione). */
export const codiceSegnalazione = (id: string) => id.slice(0, 8);
