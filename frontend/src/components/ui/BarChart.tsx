import { useLayoutEffect, useRef, useState } from "react";
import { cn } from "../../lib/cn";
import { riempimentoGrafico, type TonoGrafico } from "./area";

export interface DatoBarChart {
  /** Sotto la barra: corta («ott», «Lazio»). */
  etichetta: string;
  valore: number;
}

export interface BarChartProps {
  dati: readonly DatoBarChart[];
  /** Colore delle barre: uno stato o un'area (default `accent`). */
  tono?: TonoGrafico;
  /** Altezza totale in pixel, etichette comprese (default 180). */
  altezza?: number;
  /** Il grafico in parole per le tecnologie assistive, con i numeri che contano
   *  («Bandi in scadenza nei prossimi sei mesi: ottobre 12, novembre 8…»). */
  ariaLabel: string;
  /** Testo del valore sopra la barra (default: il numero all'italiana). */
  formatta?: (valore: number) => string;
  className?: string;
}

const SPAZIO_SOPRA = 22; // il valore sopra la barra più alta
const SPAZIO_SOTTO = 24; // le etichette sotto la base
const BARRA_MAX = 40;

const formatoNumero = new Intl.NumberFormat("it-IT");

/** Barre verticali in SVG scritto a mano: valore sopra ogni barra, etichetta
 *  sotto, la base in `line`. Il numero sta sempre scritto: il colore non porta
 *  da solo l'informazione. All'arrivo le barre crescono dalla base (niente, con
 *  il movimento ridotto). Largo quanto il contenitore. */
export function BarChart({
  dati,
  tono = "accent",
  altezza = 180,
  ariaLabel,
  formatta = (v) => formatoNumero.format(v),
  className,
}: BarChartProps) {
  const contenitore = useRef<HTMLDivElement>(null);
  const [larghezza, setLarghezza] = useState(0);

  // Il disegno è in pixel veri (testi nitidi, niente deformazioni): si misura il
  // contenitore prima del primo paint e a ogni cambio di larghezza.
  useLayoutEffect(() => {
    const el = contenitore.current;
    if (!el) return;
    const misura = () => setLarghezza(el.clientWidth);
    misura();
    if (typeof ResizeObserver === "undefined") return;
    const osservatore = new ResizeObserver(misura);
    osservatore.observe(el);
    return () => osservatore.disconnect();
  }, []);

  const massimo = Math.max(1, ...dati.map((d) => d.valore));
  const altezzaBarre = Math.max(1, altezza - SPAZIO_SOPRA - SPAZIO_SOTTO);
  const base = SPAZIO_SOPRA + altezzaBarre;
  const colonna = dati.length > 0 ? larghezza / dati.length : 0;
  const barra = Math.max(4, Math.min(BARRA_MAX, colonna * 0.56));
  const riempimento = riempimentoGrafico(tono);

  return (
    <div
      ref={contenitore}
      role="img"
      aria-label={ariaLabel}
      className={cn("w-full", className)}
      style={{ height: altezza }}
    >
      {larghezza > 0 && (
        <svg width={larghezza} height={altezza} viewBox={`0 0 ${larghezza} ${altezza}`} aria-hidden className="block overflow-visible">
          <line x1={0} x2={larghezza} y1={base + 0.5} y2={base + 0.5} className="stroke-line" strokeWidth={1} />
          {dati.map((d, i) => {
            const valore = Math.max(0, d.valore);
            const h = (valore / massimo) * altezzaBarre;
            const x = colonna * i + (colonna - barra) / 2;
            const centro = colonna * i + colonna / 2;
            return (
              <g key={`${d.etichetta}-${i}`}>
                {h > 0 && (
                  <rect
                    x={x}
                    y={base - h}
                    width={barra}
                    height={h}
                    rx={Math.min(4, barra / 4)}
                    className={cn(riempimento, "motion-safe:animate-crescita")}
                    style={{
                      transformBox: "fill-box",
                      transformOrigin: "bottom",
                      animationDelay: `${i * 40}ms`,
                    }}
                  />
                )}
                <text
                  x={centro}
                  y={base - h - 6}
                  textAnchor="middle"
                  className="fill-ink-2 text-caption tabular-nums"
                >
                  {formatta(d.valore)}
                </text>
                <text
                  x={centro}
                  y={altezza - 6}
                  textAnchor="middle"
                  className="fill-ink-3 text-caption"
                >
                  {d.etichetta}
                </text>
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}
