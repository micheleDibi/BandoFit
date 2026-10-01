import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, type LinkProps } from "react-router-dom";
import { cn } from "../../lib/cn";
import type { Area } from "./area";
import { IconChip, type IconaChip } from "./IconChip";

export interface KpiCardProps {
  /** Che cosa si conta («Bandi adatti», «AI-check disponibili»). */
  etichetta: ReactNode;
  /** Il numero (si conta da 0 al valore) o un testo già formattato («200.000 €»). */
  valore: number | string;
  /** Sotto il numero, in piccolo («3 in scadenza questa settimana»). */
  nota?: ReactNode;
  /** Icona dell'area, in un `IconChip` a destra dell'etichetta. */
  icon?: IconaChip;
  /** Colore dell'`IconChip`. */
  area?: Area;
  /** Con `to` la card intera è un link (focus visibile, ombra al passaggio).
   *  In quel caso `children` non deve contenere altri elementi interattivi. */
  to?: LinkProps["to"];
  /** Sotto il numero: una `ProgressBar`, un `ProgressRing`, un dettaglio. */
  children?: ReactNode;
  className?: string;
}

const DURATA_CONTEGGIO = 600;

function movimentoRidotto(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** Cifre decimali del valore (al massimo 2): il conteggio le mantiene. */
function decimali(n: number): number {
  const parte = String(n).split(".")[1];
  return parte ? Math.min(parte.length, 2) : 0;
}

/** Il numero mostrato durante il conteggio: da quello precedente (0 al primo
 *  arrivo) all'obiettivo in ~600 ms, con `requestAnimationFrame` e uscita
 *  morbida. Con il movimento ridotto il valore arriva subito. */
function useConteggio(obiettivo: number | null): number | null {
  const [corrente, setCorrente] = useState<number | null>(() =>
    obiettivo === null ? null : movimentoRidotto() ? obiettivo : 0,
  );
  const ultimo = useRef(0);

  useEffect(() => {
    if (obiettivo === null) return;
    if (movimentoRidotto()) {
      ultimo.current = obiettivo;
      setCorrente(obiettivo);
      return;
    }
    const partenza = ultimo.current;
    const inizio = performance.now();
    let frame = 0;
    const passo = (adesso: number) => {
      const t = Math.min(1, (adesso - inizio) / DURATA_CONTEGGIO);
      const morbido = 1 - Math.pow(1 - t, 3);
      const valore = partenza + (obiettivo - partenza) * morbido;
      ultimo.current = valore;
      setCorrente(valore);
      if (t < 1) frame = requestAnimationFrame(passo);
    };
    frame = requestAnimationFrame(passo);
    return () => cancelAnimationFrame(frame);
  }, [obiettivo]);

  return corrente;
}

/** Card di un indicatore: etichetta e icona d'area in alto, il numero grande
 *  (contato da 0 al valore), una nota e, se serve, un dettaglio sotto. Lo
 *  screen reader legge sempre il valore finale, mai i numeri di passaggio. */
export function KpiCard({ etichetta, valore, nota, icon, area, to, children, className }: KpiCardProps) {
  const numerico = typeof valore === "number" && Number.isFinite(valore);
  const conteggio = useConteggio(numerico ? valore : null);
  const formato = new Intl.NumberFormat("it-IT", {
    minimumFractionDigits: numerico ? decimali(valore) : 0,
    maximumFractionDigits: numerico ? decimali(valore) : 0,
  });
  const finale = numerico ? formato.format(valore) : String(valore);
  const mostrato = numerico && conteggio !== null ? formato.format(conteggio) : finale;

  const contenuto = (
    <>
      <div className="flex items-start justify-between gap-3">
        <div className="text-small font-medium text-ink-2">{etichetta}</div>
        {icon && <IconChip icon={icon} area={area} size="md" className="-mt-1" />}
      </div>
      <p className="text-figure text-ink">
        <span aria-hidden>{mostrato}</span>
        <span className="sr-only">{finale}</span>
      </p>
      {nota && <div className="text-small text-ink-3">{nota}</div>}
      {children && <div className="mt-1">{children}</div>}
    </>
  );

  const classi = cn(
    "flex flex-col gap-1 rounded-panel border border-line bg-sheet p-5 shadow-card",
    className,
  );

  if (to !== undefined) {
    return (
      <Link
        to={to}
        className={cn(
          classi,
          "cursor-pointer transition duration-150 ease-uscita hover:shadow-card-hover motion-safe:hover:-translate-y-0.5",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        )}
      >
        {contenuto}
      </Link>
    );
  }
  return <div className={classi}>{contenuto}</div>;
}
