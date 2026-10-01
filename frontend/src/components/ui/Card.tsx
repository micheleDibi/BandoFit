import type { HTMLAttributes } from "react";
import { cn } from "../../lib/cn";
import { areaClassi, type Area } from "./area";

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** La card risponde al passaggio del mouse (ombra più ampia e mezzo passo in
   *  su, 150 ms): solo se la card intera porta da qualche parte. */
  interattiva?: boolean;
  /** Bordo sinistro di 4px nel colore dell'area. */
  area?: Area;
}

/** Riquadro su foglio bianco con ombra `card`, per oggetti uguali da
 *  confrontare (mai un riquadro dentro un riquadro): filetto `line`, raggio
 *  `panel`, padding 20. Un `p-*` passato in `className` vince sul default. */
export function Card({ interattiva = false, area, className, ...props }: CardProps) {
  return (
    <div
      className={cn(
        "rounded-panel border border-line bg-sheet p-5 shadow-card",
        interattiva &&
          "transition duration-150 ease-uscita hover:shadow-card-hover motion-safe:hover:-translate-y-0.5",
        area && areaClassi(area).bordo,
        className,
      )}
      {...props}
    />
  );
}
