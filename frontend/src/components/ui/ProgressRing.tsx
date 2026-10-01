import { useEffect, useState, type ReactNode } from "react";
import { cn } from "../../lib/cn";
import { trattoGrafico, type TonoGrafico } from "./area";

export interface ProgressRingProps {
  value: number;
  max: number;
  /** Lato in pixel (default 64). */
  size?: number;
  /** `fit` per compatibilità ed esiti, `accent` per l'uso, `warm` per l'urgenza, o un'area. */
  tono?: TonoGrafico;
  /** Nome per le tecnologie assistive, con il valore in parole («AI-check usati: 4 su 20»). */
  label: string;
  /** Al centro, al posto del valore (es. «4/20» o una parola corta). */
  children?: ReactNode;
  className?: string;
}

/** Taglia del testo al centro, secondo il lato dell'anello. */
function testoCentro(size: number): string {
  if (size >= 88) return "text-figure";
  if (size >= 56) return "text-figure-sm";
  return "text-caption font-semibold tabular-nums";
}

/** Anello di avanzamento in SVG: pista `sunken`, arco nel tono, il valore al
 *  centro. All'arrivo l'arco si disegna da zero (niente, con il movimento
 *  ridotto). `role="img"`: il significato sta in `label`, il numero al centro
 *  è per l'occhio. */
export function ProgressRing({
  value,
  max,
  size = 64,
  tono = "accent",
  label,
  children,
  className,
}: ProgressRingProps) {
  const massimo = max > 0 ? max : 1;
  const attuale = Math.min(Math.max(value, 0), massimo);
  const tratto = Math.max(4, Math.round(size / 9));
  const raggio = (size - tratto) / 2;
  const circonferenza = 2 * Math.PI * raggio;
  const centro = size / 2;

  // L'arco parte vuoto e si riempie al primo frame: la transizione CSS fa il resto.
  const [disegnato, setDisegnato] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setDisegnato(true));
    return () => cancelAnimationFrame(id);
  }, []);
  const quota = disegnato ? attuale / massimo : 0;

  return (
    <span
      role="img"
      aria-label={label}
      className={cn("relative inline-flex shrink-0 items-center justify-center", className)}
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden className="block">
        <g transform={`rotate(-90 ${centro} ${centro})`}>
          <circle
            cx={centro}
            cy={centro}
            r={raggio}
            fill="none"
            strokeWidth={tratto}
            className="stroke-sunken"
          />
          {attuale > 0 && (
            <circle
              cx={centro}
              cy={centro}
              r={raggio}
              fill="none"
              strokeWidth={tratto}
              strokeLinecap="round"
              strokeDasharray={circonferenza}
              strokeDashoffset={circonferenza * (1 - quota)}
              className={cn(
                trattoGrafico(tono),
                "motion-safe:transition-[stroke-dashoffset] motion-safe:duration-700 motion-safe:ease-uscita",
              )}
            />
          )}
        </g>
      </svg>
      <span
        aria-hidden
        className={cn("absolute inset-0 flex items-center justify-center text-ink", testoCentro(size))}
      >
        {children ?? attuale.toLocaleString("it-IT")}
      </span>
    </span>
  );
}
