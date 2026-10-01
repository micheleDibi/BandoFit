import { Check, CircleAlert } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

export type ToastTono = "ok" | "errore";

export interface ToastOpzioni {
  /** Una frase corta, al passato: «Preferenze salvate», «Invito inviato». */
  testo: string;
  tono?: ToastTono;
  /** Millisecondi prima che sparisca (default 4000). */
  durata?: number;
}

export interface ToastApi {
  mostra: (opzioni: ToastOpzioni) => void;
}

interface ToastRecord {
  id: number;
  testo: string;
  tono: ToastTono;
  durata: number;
}

const DURATA_DEFAULT = 4000;
/** Più di così si coprirebbero a vicenda: le più vecchie escono. */
const MAX_VISIBILI = 3;

const ToastContext = createContext<ToastApi | null>(null);

/** Notifiche a comparsa per le conferme («Preferenze salvate»): in basso a
 *  sinistra, sopra la pagina, spariscono da sole. La regione `aria-live` è
 *  sempre nel DOM, così la prima notifica viene annunciata. Si monta una volta
 *  sola, in `main.tsx`, sopra `App`.
 *  Sta sotto un `<dialog>` modale aperto (il top layer vince su `z-50`): dentro
 *  una finestra gli errori vanno in `Alert`/`InlineError`; il toast si mostra
 *  dopo la chiusura, per l'esito. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);
  const contatore = useRef(0);

  const rimuovi = useCallback((id: number) => {
    setToasts((attuali) => attuali.filter((t) => t.id !== id));
  }, []);

  const mostra = useCallback(({ testo, tono = "ok", durata = DURATA_DEFAULT }: ToastOpzioni) => {
    const id = ++contatore.current;
    setToasts((attuali) => [...attuali, { id, testo, tono, durata }].slice(-MAX_VISIBILI));
  }, []);

  const api = useMemo<ToastApi>(() => ({ mostra }), [mostra]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        role="status"
        aria-live="polite"
        // Si annuncia solo la notifica aggiunta, non tutta la pila ogni volta.
        aria-atomic="false"
        className="pointer-events-none fixed bottom-4 left-4 z-50 flex max-w-[calc(100vw-2rem)] flex-col items-start gap-2"
      >
        {toasts.map((t) => (
          <ToastItem key={t.id} {...t} onFine={rimuovi} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/** `mostra({ testo, tono?, durata? })`. Va usato dentro `ToastProvider`. */
export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast va usato dentro <ToastProvider>");
  return ctx;
}

function ToastItem({
  id,
  testo,
  tono,
  durata,
  onFine,
}: ToastRecord & { onFine: (id: number) => void }) {
  const [inPausa, setInPausa] = useState(false);
  // Il tempo residuo sopravvive alle pause: al passaggio del mouse il timer si
  // ferma e riparte da dove era.
  const residuo = useRef(durata);
  const partenza = useRef(0);

  useEffect(() => {
    if (inPausa) return;
    partenza.current = Date.now();
    const timer = window.setTimeout(() => onFine(id), residuo.current);
    return () => {
      window.clearTimeout(timer);
      residuo.current = Math.max(0, residuo.current - (Date.now() - partenza.current));
    };
  }, [inPausa, id, onFine]);

  return (
    <div
      // Un errore va annunciato subito, senza aspettare che lo screen reader finisca.
      role={tono === "errore" ? "alert" : undefined}
      onMouseEnter={() => setInPausa(true)}
      onMouseLeave={() => setInPausa(false)}
      className="pointer-events-auto inline-flex items-center gap-2.5 rounded-control bg-ink px-4 py-3 text-body font-medium text-on-accent shadow-overlay"
    >
      {tono === "ok" ? (
        <Check className="size-4 shrink-0 text-fit" aria-hidden />
      ) : (
        <CircleAlert className="size-4 shrink-0 text-danger-line" aria-hidden />
      )}
      <span>{testo}</span>
    </div>
  );
}
