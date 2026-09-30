import {
  cloneElement,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type FocusEvent,
  type MouseEvent,
  type MutableRefObject,
  type ReactElement,
  type Ref,
  type RefCallback,
} from "react";
import { createPortal } from "react-dom";

export interface TooltipProps {
  /** Una riga: il nome dell'azione o del dato («Rimuovi dai salvati»). */
  testo: string;
  /** Un solo elemento focalizzabile (un pulsante, un link): viene clonato con `ref`,
   *  `aria-describedby` e i gestori di hover e focus. */
  children: ReactElement;
}

const RITARDO = 300; // ms prima di comparire
const DISTANZA = 6; // dall'elemento
const MARGINE = 8; // dal bordo della finestra

function uniscRef<T>(...refs: (Ref<T> | undefined)[]): RefCallback<T> {
  return (nodo) => {
    for (const ref of refs) {
      if (!ref) continue;
      if (typeof ref === "function") ref(nodo);
      else (ref as MutableRefObject<T | null>).current = nodo;
    }
  };
}

/** Suggerimento al passaggio del mouse e al focus, dopo 300 ms; Esc lo nasconde.
 *  Sostituisce i `title=`: il testo è sempre nel DOM (nascosto) e collegato con
 *  `aria-describedby`, così le tecnologie assistive lo leggono anche senza mouse.
 *  Solo per spiegare, mai per informazioni che servono davvero: quelle vanno nella pagina. */
export function Tooltip({ testo, children }: TooltipProps) {
  const id = useId();
  const [visibile, setVisibile] = useState(false);
  const [stile, setStile] = useState<CSSProperties>();
  const [contenitore, setContenitore] = useState<Element | null>(null);
  const ancoraRef = useRef<HTMLElement | null>(null);
  const bollaRef = useRef<HTMLDivElement>(null);
  const timer = useRef<number>();

  const mostra = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setVisibile(true), RITARDO);
  }, []);
  const nascondi = useCallback(() => {
    window.clearTimeout(timer.current);
    setVisibile(false);
  }, []);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  // Il portal va nello stesso top layer dell'elemento: dentro un <dialog> modale
  // una bolla su body resterebbe sotto il velo.
  useLayoutEffect(() => {
    setContenitore(ancoraRef.current?.closest("dialog") ?? document.body);
  }, []);

  // Sopra l'elemento, centrata; sotto se in alto non c'è spazio.
  useLayoutEffect(() => {
    if (!visibile) return;
    const a = ancoraRef.current?.getBoundingClientRect();
    const b = bollaRef.current;
    if (!a || !b) return;
    const larghezza = b.offsetWidth;
    const altezza = b.offsetHeight;
    let top = a.top - altezza - DISTANZA;
    if (top < MARGINE) top = a.bottom + DISTANZA;
    const left = Math.max(
      MARGINE,
      Math.min(a.left + a.width / 2 - larghezza / 2, window.innerWidth - larghezza - MARGINE),
    );
    setStile({ top, left });
  }, [visibile]);

  // Esc la nasconde; lo scroll pure (la bolla ha posizione fissa e non seguirebbe
  // l'elemento: meglio sparire che restare a mezz'aria).
  useEffect(() => {
    if (!visibile) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") nascondi();
    };
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", nascondi, true);
    return () => {
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", nascondi, true);
    };
  }, [visibile, nascondi]);

  const props = children.props as {
    onMouseEnter?: (e: MouseEvent<HTMLElement>) => void;
    onMouseLeave?: (e: MouseEvent<HTMLElement>) => void;
    onFocus?: (e: FocusEvent<HTMLElement>) => void;
    onBlur?: (e: FocusEvent<HTMLElement>) => void;
    "aria-describedby"?: string;
  };
  const refFiglio = (children as unknown as { ref?: Ref<HTMLElement> }).ref;
  const figlio = cloneElement(children, {
    ref: uniscRef(ancoraRef, refFiglio),
    "aria-describedby": [props["aria-describedby"], id].filter(Boolean).join(" "),
    onMouseEnter: (e: MouseEvent<HTMLElement>) => {
      props.onMouseEnter?.(e);
      mostra();
    },
    onMouseLeave: (e: MouseEvent<HTMLElement>) => {
      props.onMouseLeave?.(e);
      nascondi();
    },
    onFocus: (e: FocusEvent<HTMLElement>) => {
      props.onFocus?.(e);
      mostra();
    },
    onBlur: (e: FocusEvent<HTMLElement>) => {
      props.onBlur?.(e);
      nascondi();
    },
  });

  return (
    <>
      {figlio}
      {contenitore &&
        createPortal(
          <div
            ref={bollaRef}
            id={id}
            role="tooltip"
            hidden={!visibile}
            style={stile}
            className="pointer-events-none fixed z-50 max-w-64 rounded-mark bg-ink px-2 py-1 text-caption text-on-accent shadow-overlay"
          >
            {testo}
          </div>,
          contenitore,
        )}
    </>
  );
}
