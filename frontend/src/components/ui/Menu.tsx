import { MoreVertical } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { cn } from "../../lib/cn";

/** Menu a discesa accessibile (overflow "⋮"). Il pannello vive in un PORTAL con
 *  posizione `fixed`: dentro una tabella con `overflow-x-auto`/`overflow-hidden`
 *  un pannello `absolute` verrebbe tagliato. Tastiera: ↑/↓/Home/End tra le voci,
 *  Esc chiude e riporta il focus al trigger, click fuori chiude. Nessuna
 *  dipendenza esterna. Aspetto: foglio con raggio `panel` e `shadow-overlay`,
 *  voci alte 36px. */

interface MenuCtx {
  close: (returnFocus?: boolean) => void;
}
const MenuContext = createContext<MenuCtx | null>(null);

export interface MenuProps {
  /** aria-label del bottone trigger (obbligatoria: è icon-only). */
  label: string;
  children: ReactNode;
  /** Contenuto del trigger; default: icona "⋮". */
  triggerIcon?: ReactNode;
  triggerClassName?: string;
}

export function Menu({ label, children, triggerIcon, triggerClassName }: MenuProps) {
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState<{ top: number; right: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  const close = useCallback((returnFocus = false) => {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  }, []);

  // Apre calcolando la posizione SUBITO (stesso batch di `open`): così il
  // pannello monta al primo render e l'effetto di focus lo trova già in DOM
  // (con coords in un secondo render, la prima apertura non riceverebbe il
  // focus da tastiera).
  const openMenu = useCallback(() => {
    const t = triggerRef.current?.getBoundingClientRect();
    if (t) setCoords({ top: t.bottom + 4, right: window.innerWidth - t.right });
    setOpen(true);
  }, []);

  // Riposizionamento su scroll/resize: il pannello segue il trigger.
  useLayoutEffect(() => {
    if (!open) return;
    const reposition = () => {
      const t = triggerRef.current?.getBoundingClientRect();
      if (t) setCoords({ top: t.bottom + 4, right: window.innerWidth - t.right });
    };
    window.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
    return () => {
      window.removeEventListener("scroll", reposition, true);
      window.removeEventListener("resize", reposition);
    };
  }, [open]);

  // All'apertura, focus sulla prima voce abilitata.
  useEffect(() => {
    if (!open) return;
    const first = panelRef.current?.querySelector<HTMLElement>(
      '[role="menuitem"]:not([aria-disabled="true"])',
    );
    first?.focus();
  }, [open]);

  // Click fuori (trigger + pannello sono in due sottoalberi: il pannello è nel portal).
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (!panelRef.current?.contains(target) && !triggerRef.current?.contains(target)) {
        close();
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open, close]);

  const moveFocus = (dir: 1 | -1 | "first" | "last") => {
    const items = Array.from(
      panelRef.current?.querySelectorAll<HTMLElement>(
        '[role="menuitem"]:not([aria-disabled="true"])',
      ) ?? [],
    );
    if (items.length === 0) return;
    const idx = items.indexOf(document.activeElement as HTMLElement);
    let next: number;
    if (dir === "first") next = 0;
    else if (dir === "last") next = items.length - 1;
    else next = (idx + dir + items.length) % items.length;
    items[next]?.focus();
  };

  const onPanelKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      close(true);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      moveFocus(1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      moveFocus(-1);
    } else if (e.key === "Home") {
      e.preventDefault();
      moveFocus("first");
    } else if (e.key === "End") {
      e.preventDefault();
      moveFocus("last");
    } else if (e.key === "Tab") {
      // Tab esce dal menu: si chiude riportando il focus al trigger (senza
      // preventDefault il focus cadrebbe a inizio documento — il menuitem
      // focalizzato viene smontato prima che il browser risolva il tab-stop).
      e.preventDefault();
      close(true);
    }
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => (open ? close() : openMenu())}
        onKeyDown={(e) => {
          if (!open && (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ")) {
            e.preventDefault();
            openMenu();
          }
        }}
        className={cn(
          "inline-flex size-9 cursor-pointer items-center justify-center rounded-control text-ink-2",
          "transition-colors duration-150 hover:bg-sunken hover:text-ink",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
          open && "bg-sunken text-ink",
          triggerClassName,
        )}
      >
        {triggerIcon ?? <MoreVertical className="size-4" aria-hidden />}
      </button>

      {open &&
        coords &&
        createPortal(
          <div
            ref={panelRef}
            id={menuId}
            role="menu"
            aria-label={label}
            onKeyDown={onPanelKeyDown}
            style={{ position: "fixed", top: coords.top, right: coords.right }}
            className="z-50 flex min-w-[220px] flex-col gap-0.5 rounded-panel border border-line bg-sheet p-1.5 shadow-overlay motion-safe:animate-entrata"
          >
            <MenuContext.Provider value={{ close }}>{children}</MenuContext.Provider>
          </div>,
          document.body,
        )}
    </>
  );
}

export interface MenuItemProps {
  children: ReactNode;
  onSelect?: () => void;
  disabled?: boolean;
  /** Tooltip nativo (usato per spiegare perché una voce è disabilitata). */
  title?: string;
  danger?: boolean;
  icon?: ReactNode;
}

export function MenuItem({ children, onSelect, disabled, title, danger, icon }: MenuItemProps) {
  const ctx = useContext(MenuContext);
  return (
    <button
      type="button"
      role="menuitem"
      tabIndex={-1}
      aria-disabled={disabled || undefined}
      title={title}
      onClick={() => {
        if (disabled) return;
        onSelect?.();
        ctx?.close();
      }}
      className={cn(
        "flex h-9 w-full items-center gap-2.5 rounded-md px-2.5 text-left text-body font-medium",
        "transition-colors duration-150",
        // Focus roving (programmatico): fondo `sunken` più un anello interno
        // (non sporge dal pannello), così il segno non è il solo colore.
        "focus:outline-2 focus:-outline-offset-2",
        disabled
          ? "cursor-not-allowed text-ink-3"
          : danger
            ? "cursor-pointer text-danger hover:bg-danger-soft focus:bg-danger-soft focus:outline-danger"
            : "cursor-pointer text-ink hover:bg-sunken focus:bg-sunken focus:outline-accent",
      )}
    >
      {icon && (
        <span className="shrink-0 text-current [&_svg]:size-4" aria-hidden>
          {icon}
        </span>
      )}
      {children}
    </button>
  );
}

export function MenuSeparator() {
  return <div role="separator" className="my-1 border-t border-line" />;
}
