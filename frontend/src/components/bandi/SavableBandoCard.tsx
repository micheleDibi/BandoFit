import { cn } from "../../lib/cn";
import type { BandoListItem } from "../../types";
import { BandoCard } from "./BandoCard";
import { SaveBandoButton } from "./SaveBandoButton";

/** Card bando con il toggle di salvataggio SOVRAPPOSTO come fratello del
 *  link (mai annidato dentro: la card è interamente un <Link> e il repo
 *  vieta gli elementi interattivi annidati). */
export function SavableBandoCard({
  bando,
  className,
}: {
  bando: BandoListItem;
  className?: string;
}) {
  return (
    // h-full: nella griglia il wrapper viene stirato e la card-link lo riempie
    // (senza, le card di una stessa riga avrebbero altezze diverse).
    <div className={cn("relative h-full", className)}>
      <BandoCard bando={bando} />
      <SaveBandoButton bando={{ id: bando.id, slug: bando.slug }} />
    </div>
  );
}
