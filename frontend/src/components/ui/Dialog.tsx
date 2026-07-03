import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
}

/** Modale basata sull'elemento <dialog> nativo: focus trap ed Esc gratis. */
export function Dialog({ open, onClose, title, children, footer }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => {
        // click sul backdrop = chiusura
        if (e.target === ref.current) onClose();
      }}
      className="m-auto w-full max-w-md rounded-xl border border-slate-200 bg-white p-0 shadow-xl backdrop:bg-brand-950/50"
    >
      <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
        <h2 className="font-display text-base font-semibold text-slate-900">{title}</h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="Chiudi"
          className="cursor-pointer rounded-md p-1 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 focus-visible:outline-2 focus-visible:outline-brand-500"
        >
          <X className="size-5" aria-hidden />
        </button>
      </div>
      <div className="px-5 py-4 text-sm text-slate-600">{children}</div>
      {footer && (
        <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-4">{footer}</div>
      )}
    </dialog>
  );
}
