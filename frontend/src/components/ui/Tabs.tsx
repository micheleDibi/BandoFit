import {
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type HTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { cn } from "../../lib/cn";

export interface Scheda<T extends string = string> {
  id: T;
  label: string;
  count?: number;
}

/** Generico sull'unione degli id: `onChange` accetta il `setTab` di `useTab<T>` senza cast. */
export interface TabsProps<T extends string = string> {
  tabs: readonly Scheda<T>[];
  /** Id della scheda attiva (dall'URL, tramite `useTab`). */
  attivo: T;
  onChange: (id: T) => void;
  ariaLabel: string;
  /** Prefisso degli id DOM, lo stesso passato ai `TabPanel`: collega ogni scheda al
   *  suo pannello con `aria-controls`. Senza, gli id vengono da `useId()` (due `Tabs`
   *  nella stessa pagina non collidono) e nessun `aria-controls` resta pendente. */
  prefisso?: string;
  className?: string;
}

/** Id DOM del pulsante e del pannello di una scheda (condivisi da `Tabs` e `TabPanel`). */
function idScheda(prefisso: string, id: string) {
  return { tab: `${prefisso}-tab-${id}`, panel: `${prefisso}-panel-${id}` };
}

/** Schede: filetto sotto, scheda attiva in `accent` con l'indicatore che scorre
 *  fin sotto di lei (250 ms; di scatto con il movimento ridotto); i contatori
 *  sono pillole. Non legge l'URL: lo fa `useTab`, che passa `attivo` e
 *  `onChange`. Frecce, Home e End spostano il focus e la selezione. */
export function Tabs<T extends string>({
  tabs,
  attivo,
  onChange,
  ariaLabel,
  prefisso,
  className,
}: TabsProps<T>) {
  const pulsanti = useRef<(HTMLButtonElement | null)[]>([]);
  const idAutomatico = useId();
  const base = prefisso ?? idAutomatico;
  const indiceAttivo = tabs.findIndex((t) => t.id === attivo);

  // L'indicatore: posizione e larghezza della scheda attiva, misurate prima del
  // paint e di nuovo quando le schede cambiano larghezza (font caricati,
  // contatori che arrivano, finestra ridimensionata).
  const lista = useRef<HTMLDivElement>(null);
  const [indicatore, setIndicatore] = useState<{ left: number; width: number } | null>(null);
  const firmaSchede = tabs.map((t) => `${t.id}:${t.label}:${t.count ?? ""}`).join("|");
  useLayoutEffect(() => {
    const misura = () => {
      const scheda = indiceAttivo >= 0 ? pulsanti.current[indiceAttivo] : null;
      const nuovo = scheda ? { left: scheda.offsetLeft, width: scheda.offsetWidth } : null;
      // Stessa misura = stesso stato: niente render a ogni notifica dell'osservatore.
      setIndicatore((prima) =>
        prima && nuovo && prima.left === nuovo.left && prima.width === nuovo.width ? prima : nuovo,
      );
    };
    misura();
    if (typeof ResizeObserver === "undefined" || !lista.current) return;
    const osservatore = new ResizeObserver(misura);
    osservatore.observe(lista.current);
    for (const pulsante of pulsanti.current) if (pulsante) osservatore.observe(pulsante);
    return () => osservatore.disconnect();
  }, [indiceAttivo, firmaSchede]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const n = tabs.length;
    if (n === 0) return;
    let prossimo: number | null = null;
    if (e.key === "Home") prossimo = 0;
    else if (e.key === "End") prossimo = n - 1;
    // Con `attivo` non valido (nessuna scheda selezionata) le frecce partono dalla prima.
    else if (e.key === "ArrowRight") prossimo = indiceAttivo === -1 ? 0 : (indiceAttivo + 1) % n;
    else if (e.key === "ArrowLeft") prossimo = indiceAttivo === -1 ? 0 : (indiceAttivo - 1 + n) % n;
    if (prossimo === null) return;
    e.preventDefault();
    onChange(tabs[prossimo].id);
    pulsanti.current[prossimo]?.focus();
  };

  return (
    <div
      ref={lista}
      role="tablist"
      aria-label={ariaLabel}
      onKeyDown={onKeyDown}
      className={cn("relative flex gap-6 overflow-x-auto border-b border-line", className)}
    >
      {tabs.map((scheda, i) => {
        const selezionata = scheda.id === attivo;
        // Se nessuna scheda è attiva (id non valido) la prima resta raggiungibile con Tab.
        const raggiungibile = indiceAttivo === -1 ? i === 0 : selezionata;
        const ids = idScheda(base, scheda.id);
        return (
          <button
            key={scheda.id}
            ref={(el) => {
              pulsanti.current[i] = el;
            }}
            type="button"
            role="tab"
            id={ids.tab}
            aria-selected={selezionata}
            aria-controls={prefisso ? ids.panel : undefined}
            tabIndex={raggiungibile ? 0 : -1}
            onClick={() => onChange(scheda.id)}
            className={cn(
              "inline-flex h-10 shrink-0 cursor-pointer items-center gap-2 whitespace-nowrap font-medium focus-visible:-outline-offset-2",
              "transition-colors duration-150 ease-uscita",
              selezionata ? "text-accent" : "text-ink-2 hover:text-ink",
            )}
          >
            {scheda.label}
            {scheda.count !== undefined && (
              <span
                className={cn(
                  "inline-flex h-5 min-w-5 items-center justify-center rounded-pill px-1.5 text-caption font-semibold tabular-nums",
                  "transition-colors duration-150 ease-uscita",
                  selezionata ? "bg-accent text-on-accent" : "bg-accent-soft text-accent-hover",
                )}
              >
                {scheda.count}
              </span>
            )}
          </button>
        );
      })}
      {indicatore && (
        <span
          aria-hidden
          className="pointer-events-none absolute bottom-0 h-0.5 rounded-pill bg-accent motion-safe:transition-[left,width] motion-safe:duration-250 motion-safe:ease-uscita"
          style={{ left: indicatore.left, width: indicatore.width }}
        />
      )}
    </div>
  );
}

export interface TabPanelProps<T extends string = string> extends HTMLAttributes<HTMLDivElement> {
  /** Id della scheda a cui appartiene. */
  id: T;
  /** Id della scheda attiva: il pannello si monta solo se coincide. */
  attivo: T;
  /** Lo stesso `prefisso` passato a `Tabs`: è ciò che collega pannello e scheda. */
  prefisso: string;
  children: ReactNode;
}

/** Pannello di una scheda: `role="tabpanel"` collegato al pulsante della scheda
 *  tramite `prefisso`; montato solo quando la scheda è attiva (le query delle
 *  altre non partono). */
export function TabPanel<T extends string>({
  id,
  attivo,
  prefisso,
  className,
  children,
  ...props
}: TabPanelProps<T>) {
  if (attivo !== id) return null;
  const ids = idScheda(prefisso, id);
  return (
    <div
      role="tabpanel"
      id={ids.panel}
      aria-labelledby={ids.tab}
      className={cn("flex flex-col gap-6", className)}
      {...props}
    >
      {children}
    </div>
  );
}
