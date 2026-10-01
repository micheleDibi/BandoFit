import {
  cloneElement,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
  type MutableRefObject,
  type ReactElement,
  type ReactNode,
  type Ref,
  type RefCallback,
} from "react";
import { createPortal } from "react-dom";
import { cn } from "../../lib/cn";

/** Pannello a comparsa ancorato a un pulsante: la base dei filtri a menu (e, in
 *  ondata 4, di `Menu`). Nessuna libreria: il pannello vive in un portal con
 *  posizione `fixed` calcolata dal rettangolo del trigger, così non viene tagliato
 *  da un contenitore con `overflow`. Si chiude con Esc (focus al trigger), con un
 *  clic fuori e con `chiudi()` di `usePopover()` dai figli («Applica»). */

export type PopoverAlign = "start" | "end";

export interface PopoverProps {
  /** Il pulsante che apre il pannello. Viene clonato con `ref`, `onClick` e gli
   *  `aria-*`: il componente deve fare `forwardRef` e passare le props al `<button>`
   *  (`Button`, `IconButton` e `Filter` lo fanno). */
  trigger: ReactElement;
  children: ReactNode;
  /** Bordo del pannello allineato al bordo sinistro (`start`) o destro (`end`) del trigger. */
  align?: PopoverAlign;
  /** Nome del pannello per le tecnologie assistive («Filtro per stato»). */
  label?: string;
  /** Modalità controllata, opzionale: senza `open` il pannello si gestisce da solo. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Classi del pannello (p.es. una larghezza o un padding diverso). */
  className?: string;
}

interface PopoverCtx {
  aperto: boolean;
  /** Chiude il pannello; se il focus era dentro, torna al trigger. */
  chiudi: () => void;
}

const PopoverContext = createContext<PopoverCtx | null>(null);

/** Per i figli del pannello: `{ aperto, chiudi }`. */
export function usePopover(): PopoverCtx {
  const ctx = useContext(PopoverContext);
  if (!ctx) throw new Error("usePopover va usato dentro <Popover>");
  return ctx;
}

const DISTANZA = 4; // fra trigger e pannello
const MARGINE = 8; // dal bordo della finestra

const FOCALIZZABILI =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Elementi raggiungibili con Tab: di un gruppo di radio conta solo quello scelto
 *  (o il primo, se nessuno lo è), come fa il browser. */
function tabbabili(pannello: HTMLElement): HTMLElement[] {
  const tutti = Array.from(pannello.querySelectorAll<HTMLElement>(FOCALIZZABILI));
  return tutti.filter((el) => {
    if (!(el instanceof HTMLInputElement) || el.type !== "radio" || !el.name) return true;
    const gruppo = tutti.filter(
      (altro): altro is HTMLInputElement =>
        altro instanceof HTMLInputElement && altro.type === "radio" && altro.name === el.name,
    );
    return el === (gruppo.find((r) => r.checked) ?? gruppo[0]);
  });
}

/** Il focus è su un radio dello stesso gruppo di `riferimento`. */
function stessoGruppoRadio(attivo: Element | null, riferimento: HTMLElement): boolean {
  return (
    attivo instanceof HTMLInputElement &&
    riferimento instanceof HTMLInputElement &&
    attivo.type === "radio" &&
    riferimento.type === "radio" &&
    attivo.name !== "" &&
    attivo.name === riferimento.name
  );
}

function uniscRef<T>(...refs: (Ref<T> | undefined)[]): RefCallback<T> {
  return (nodo) => {
    for (const ref of refs) {
      if (!ref) continue;
      if (typeof ref === "function") ref(nodo);
      else (ref as MutableRefObject<T | null>).current = nodo;
    }
  };
}

/** Sotto il trigger; sopra se sotto non c'è spazio e sopra ce n'è di più. In
 *  orizzontale il pannello resta dentro la finestra con un margine di 8px. */
function calcolaPosizione(
  trigger: DOMRect,
  pannello: { width: number; height: number },
  align: PopoverAlign,
): CSSProperties {
  const spazioSotto = window.innerHeight - trigger.bottom;
  const sopra =
    spazioSotto < pannello.height + DISTANZA + MARGINE && trigger.top > spazioSotto;
  const verticale: CSSProperties = sopra
    ? { bottom: window.innerHeight - trigger.top + DISTANZA, maxHeight: trigger.top - DISTANZA - MARGINE }
    : { top: trigger.bottom + DISTANZA, maxHeight: spazioSotto - DISTANZA - MARGINE };
  // `clientWidth` esclude la barra di scorrimento verticale: con `innerWidth` il
  // pannello allineato a destra ci finirebbe sotto.
  const larghezzaFinestra = document.documentElement.clientWidth;
  const limite = Math.max(MARGINE, larghezzaFinestra - pannello.width - MARGINE);
  const orizzontale: CSSProperties =
    align === "end"
      ? { right: Math.min(larghezzaFinestra - trigger.right, limite) }
      : { left: Math.min(trigger.left, limite) };
  return { position: "fixed", ...verticale, ...orizzontale };
}

export function Popover({
  trigger,
  children,
  align = "start",
  label,
  open: openProp,
  onOpenChange,
  className,
}: PopoverProps) {
  const [apertoInterno, setApertoInterno] = useState(false);
  const controllato = openProp !== undefined;
  const aperto = controllato ? openProp : apertoInterno;
  const [stile, setStile] = useState<CSSProperties>({ position: "fixed", top: 0, left: 0 });
  const triggerRef = useRef<HTMLElement | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

  const imposta = useCallback(
    (valore: boolean) => {
      if (!controllato) setApertoInterno(valore);
      onOpenChange?.(valore);
    },
    [controllato, onOpenChange],
  );

  // Vero quando la chiusura parte da qui (Esc, clic fuori, `chiudi()`): il focus
  // è già stato gestito e l'effetto sotto non deve intervenire.
  const chiusuraInterna = useRef(false);

  const chiudi = useCallback(
    (riportaFocus?: boolean) => {
      const focusDentro = panelRef.current?.contains(document.activeElement) ?? false;
      chiusuraInterna.current = true;
      imposta(false);
      if (riportaFocus ?? focusDentro) triggerRef.current?.focus();
    },
    [imposta],
  );

  // Chiusura decisa dal genitore (`open` → false senza `chiudi`): il pannello si
  // smonta e il focus cadrebbe sul body; torna al trigger.
  const eraAperto = useRef(false);
  useEffect(() => {
    if (eraAperto.current && !aperto && !chiusuraInterna.current) {
      const attivo = document.activeElement;
      if (!attivo || attivo === document.body) triggerRef.current?.focus();
    }
    chiusuraInterna.current = false;
    eraAperto.current = aperto;
  }, [aperto]);

  // Posizione: calcolata prima del paint all'apertura, poi seguendo scroll e resize.
  const riposiziona = useCallback(() => {
    const t = triggerRef.current?.getBoundingClientRect();
    const p = panelRef.current;
    if (!t || !p) return;
    // `scrollHeight` è l'altezza del contenuto anche quando `maxHeight` lo comprime:
    // così la scelta sopra/sotto non oscilla fra un calcolo e l'altro.
    setStile(calcolaPosizione(t, { width: p.offsetWidth, height: p.scrollHeight }, align));
  }, [align]);

  useLayoutEffect(() => {
    if (!aperto) return;
    riposiziona();
    window.addEventListener("scroll", riposiziona, true);
    window.addEventListener("resize", riposiziona);
    return () => {
      window.removeEventListener("scroll", riposiziona, true);
      window.removeEventListener("resize", riposiziona);
    };
  }, [aperto, riposiziona]);

  // All'apertura il focus va al primo elemento del pannello (o al pannello stesso).
  useEffect(() => {
    if (!aperto) return;
    const primo = panelRef.current ? tabbabili(panelRef.current)[0] : undefined;
    (primo ?? panelRef.current)?.focus();
  }, [aperto]);

  // Clic fuori (trigger e pannello stanno in due sottoalberi: il pannello è nel portal).
  useEffect(() => {
    if (!aperto) return;
    const onDown = (e: globalThis.MouseEvent) => {
      const target = e.target as Node;
      if (!panelRef.current?.contains(target) && !triggerRef.current?.contains(target)) {
        chiudi(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [aperto, chiudi]);

  const onPanelKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      chiudi(true);
      return;
    }
    if (e.key !== "Tab") return;
    // Tab e Maiusc+Tab girano dentro il pannello: nel portal l'ordine naturale
    // porterebbe il focus a inizio o fine documento.
    const elementi = panelRef.current ? tabbabili(panelRef.current) : [];
    if (elementi.length === 0) {
      e.preventDefault();
      return;
    }
    const primo = elementi[0];
    const ultimo = elementi[elementi.length - 1];
    const attivo = document.activeElement;
    // Un radio del gruppo del primo/ultimo elemento conta come quell'elemento.
    const suPrimo =
      attivo === primo || attivo === panelRef.current || stessoGruppoRadio(attivo, primo);
    const suUltimo = attivo === ultimo || stessoGruppoRadio(attivo, ultimo);
    if (e.shiftKey && suPrimo) {
      e.preventDefault();
      ultimo.focus();
    } else if (!e.shiftKey && suUltimo) {
      e.preventDefault();
      primo.focus();
    }
  };

  const propsTrigger = trigger.props as {
    onClick?: (e: MouseEvent<HTMLElement>) => void;
  };
  const refTrigger = (trigger as unknown as { ref?: Ref<HTMLElement> }).ref;
  const triggerClonato = cloneElement(trigger, {
    ref: uniscRef(triggerRef, refTrigger),
    onClick: (e: MouseEvent<HTMLElement>) => {
      propsTrigger.onClick?.(e);
      if (!e.defaultPrevented) {
        if (aperto) chiudi(false);
        else imposta(true);
      }
    },
    "aria-expanded": aperto,
    "aria-haspopup": "dialog",
    "aria-controls": aperto ? panelId : undefined,
  });

  // Dentro un <dialog> modale (Drawer, Dialog) il pannello deve stare nello stesso
  // top layer: un portal su body finirebbe sotto il velo, inerte.
  const contenitore = triggerRef.current?.closest("dialog") ?? document.body;

  // Nome del pannello: `label`, altrimenti quello del trigger (aria-label o testo),
  // così `role="dialog"` ha sempre un nome.
  const nomeTrigger =
    triggerRef.current?.getAttribute("aria-label") ||
    triggerRef.current?.textContent?.trim() ||
    undefined;
  const nome = label ?? nomeTrigger;

  return (
    <>
      {triggerClonato}
      {aperto &&
        createPortal(
          <div
            ref={panelRef}
            id={panelId}
            role="dialog"
            aria-label={nome}
            tabIndex={-1}
            onKeyDown={onPanelKeyDown}
            style={stile}
            className={cn(
              "z-50 min-w-56 overflow-auto rounded-panel border border-line bg-sheet p-1.5 text-body text-ink shadow-overlay outline-none motion-safe:animate-entrata",
              className,
            )}
          >
            <PopoverContext.Provider value={{ aperto, chiudi: () => chiudi() }}>
              {children}
            </PopoverContext.Provider>
          </div>,
          contenitore,
        )}
    </>
  );
}
