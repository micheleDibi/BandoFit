import { Bell } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMarkNotificationsRead, useNotifications } from "../../hooks/useNotifications";
import { cn } from "../../lib/cn";
import { NOTIFICHE_COPY } from "../../lib/copy";
import { formatDateTime } from "../../lib/format";
import type { Notifica } from "../../types";

/** Campanella della navbar: badge con le non lette, pannello a tendina con la
 *  prima pagina di notifiche. Stesso comportamento di chiusura di NavMenu
 *  (selezione, click fuori, Esc). */
export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const { data, isError } = useNotifications();
  const markRead = useMarkNotificationsRead();

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const nonLette = data?.non_lette ?? 0;

  const handleItemClick = (notifica: Notifica) => {
    if (!notifica.read_at) markRead.mutate({ ids: [notifica.id] });
    setOpen(false);
    if (notifica.url) navigate(notifica.url);
  };

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={nonLette > 0 ? NOTIFICHE_COPY.apriConNonLette(nonLette) : NOTIFICHE_COPY.apri}
        className="relative inline-flex size-9 cursor-pointer items-center justify-center rounded-lg text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-2 focus-visible:outline-brand-500"
      >
        <Bell className="size-4.5" aria-hidden />
        {nonLette > 0 && (
          <span
            aria-hidden
            className="absolute right-1 top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-brand-500 px-1 text-[10px] font-semibold leading-none text-white"
          >
            {nonLette > 9 ? "9+" : nonLette}
          </span>
        )}
      </button>

      {open && (
        <div
          role="menu"
          aria-label={NOTIFICHE_COPY.titoloPannello}
          className="absolute right-0 top-full z-50 mt-1 w-80 max-w-[calc(100vw-2rem)] rounded-xl border border-slate-200 bg-white shadow-lg"
        >
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
            <p className="text-sm font-semibold text-slate-900">
              {NOTIFICHE_COPY.titoloPannello}
            </p>
            {nonLette > 0 && (
              <button
                type="button"
                onClick={() => markRead.mutate({ all: true })}
                className="cursor-pointer text-xs font-medium text-brand-600 hover:text-brand-700"
              >
                {NOTIFICHE_COPY.segnaTutteLette}
              </button>
            )}
          </div>

          <div className="max-h-96 overflow-y-auto p-1">
            {isError ? (
              <p className="px-3 py-4 text-sm text-slate-500">
                {NOTIFICHE_COPY.erroreCaricamento}
              </p>
            ) : !data || data.items.length === 0 ? (
              <p className="px-3 py-4 text-sm text-slate-500">{NOTIFICHE_COPY.vuoto}</p>
            ) : (
              data.items.map((notifica) => (
                <button
                  key={notifica.id}
                  type="button"
                  role="menuitem"
                  onClick={() => handleItemClick(notifica)}
                  className={cn(
                    "flex w-full cursor-pointer items-start gap-2.5 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-slate-100",
                    !notifica.read_at && "bg-brand-50/50",
                  )}
                >
                  <span
                    aria-hidden
                    className={cn(
                      "mt-1.5 size-2 shrink-0 rounded-full",
                      notifica.read_at ? "bg-transparent" : "bg-brand-500",
                    )}
                  />
                  <span className="min-w-0">
                    <span
                      className={cn(
                        "block truncate text-sm",
                        notifica.read_at ? "text-slate-600" : "font-medium text-slate-900",
                      )}
                    >
                      {notifica.titolo}
                    </span>
                    {notifica.corpo && (
                      <span className="mt-0.5 block text-xs text-slate-500">
                        {notifica.corpo}
                      </span>
                    )}
                    <span className="tabular mt-0.5 block text-xs text-slate-400">
                      {formatDateTime(notifica.created_at)}
                    </span>
                  </span>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
