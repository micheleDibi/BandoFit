import { X } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";
import { cn } from "../../lib/cn";
import { IconButton } from "./IconButton";

export type DrawerLato = "sinistra" | "destra";

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  lato?: DrawerLato;
  /** Nome del cassetto («Menu»): visibile nella testata, o solo per le tecnologie
   *  assistive quando c'è `intestazione`. */
  titolo: string;
  children: ReactNode;
  /** Al posto del titolo visibile nella testata (il logo, nel menu mobile). */
  intestazione?: ReactNode;
  className?: string;
}

/** Cassetto laterale su `<dialog>` nativo, largo 320px: focus intrappolato, Esc e
 *  ritorno del focus al pulsante che lo ha aperto sono gratis; il velo lo dà la
 *  regola globale `dialog::backdrop` di `index.css` (token `veil`). Clic sul velo
 *  = chiusura. Nessuna animazione d'ingresso. */
export function Drawer({
  open,
  onClose,
  lato = "sinistra",
  titolo,
  children,
  intestazione,
  className,
}: DrawerProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titoloId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  // Il <dialog> modale non blocca lo scroll della pagina sotto: lo si ferma finché
  // il cassetto è aperto e si ripristina com'era.
  useEffect(() => {
    if (!open) return;
    const precedente = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = precedente;
    };
  }, [open]);

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => {
        // clic sul velo = chiusura (il velo è il <dialog> stesso, fuori dal contenuto)
        if (e.target === ref.current) onClose();
      }}
      aria-labelledby={intestazione ? undefined : titoloId}
      aria-label={intestazione ? titolo : undefined}
      className={cn(
        // Sovrascrive la centratura del <dialog> modale: attaccato a un lato, alto quanto la finestra.
        "fixed inset-y-0 m-0 h-dvh max-h-none w-80 max-w-full border-0 bg-desk p-0 text-body text-ink shadow-overlay",
        lato === "sinistra" ? "left-0 right-auto" : "left-auto right-0",
        className,
      )}
    >
      <div className="flex h-full flex-col">
        <div className="flex h-14 shrink-0 items-center justify-between gap-2 pl-4 pr-2">
          {intestazione ? (
            <div className="min-w-0">{intestazione}</div>
          ) : (
            <h2 id={titoloId} className="truncate text-title-section">
              {titolo}
            </h2>
          )}
          <IconButton label="Chiudi" icon={<X />} onClick={onClose} />
        </div>
        <div className="min-h-0 grow overflow-y-auto px-3 pb-4">{children}</div>
      </div>
    </dialog>
  );
}
