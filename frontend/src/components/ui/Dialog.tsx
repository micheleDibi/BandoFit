import { X } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";
import { cn } from "../../lib/cn";
import { IconButton } from "./IconButton";

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  /** `false` blocca Esc, click sul backdrop e la X: la modale si chiude solo
   *  dai suoi bottoni. Serve quando chiuderla perderebbe qualcosa di prezioso
   *  — p.es. l'esito di una chiamata a pagamento ancora in volo. */
  dismissible?: boolean;
  /** Larghezza extra per i contenuti a scheda (anteprima import). */
  size?: "md" | "lg";
}

/** Modale basata sull'elemento <dialog> nativo: focus trap ed Esc gratis.
 *  Foglio con raggio `panel` e `shadow-overlay`; il velo lo dà la regola
 *  globale `dialog::backdrop` (token `veil`). */
export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  dismissible = true,
  size = "md",
}: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  // Il titolo dà il nome accessibile alla finestra (vale anche per ConfirmDialog).
  const titoloId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titoloId}
      onClose={onClose}
      // `cancel` precede `close` ed è ciò che l'Esc scatena: prevenendolo la
      // modale non si chiude. `close()` chiamato da noi non passa di qui.
      onCancel={(e) => {
        if (!dismissible) e.preventDefault();
      }}
      // Chrome ignora il `preventDefault` sul `cancel` a un secondo Esc ravvicinato
      // (protezione anti-abuso dei close watcher): si ferma il tasto alla fonte.
      onKeyDown={(e) => {
        if (!dismissible && e.key === "Escape") e.preventDefault();
      }}
      onClick={(e) => {
        // click sul backdrop = chiusura
        if (dismissible && e.target === ref.current) onClose();
      }}
      className={cn(
        "m-auto w-full rounded-panel bg-sheet p-0 text-ink shadow-overlay",
        size === "lg" ? "max-w-[640px]" : "max-w-[480px]",
      )}
    >
      <div className="flex flex-col gap-4 p-6">
        <div className="flex items-start justify-between gap-4">
          <h2 id={titoloId} className="text-title-section text-ink">
            {title}
          </h2>
          {dismissible && (
            <IconButton
              label="Chiudi"
              icon={<X />}
              size="sm"
              onClick={onClose}
              className="-mr-2 -mt-1"
            />
          )}
        </div>
        <div className="text-body text-ink-2">{children}</div>
        {footer && <div className="flex flex-wrap justify-end gap-2">{footer}</div>}
      </div>
    </dialog>
  );
}
