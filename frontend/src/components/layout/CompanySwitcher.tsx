import { Building2, Check, ChevronDown, Settings2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useActiveCompany } from "../../hooks/useActiveCompany";
import { cn } from "../../lib/cn";

/** Selettore dell'azienda attiva (solo per gli Advisor multi-azienda): cambia
 *  il contesto di TUTTA l'app. Nascosto per gli altri piani. */
export function CompanySwitcher() {
  const { isMulti, companies, activeCompanyId, setActiveCompany } = useActiveCompany();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

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

  if (!isMulti) return null;

  const active = companies.find((c) => c.id === activeCompanyId);
  const label = active?.ragione_sociale ?? "Scegli azienda";

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={`Azienda attiva: ${label}. Cambia azienda`}
        className="inline-flex h-9 max-w-52 cursor-pointer items-center gap-2 rounded-lg border border-slate-300 px-3 text-sm font-medium text-slate-700 transition-colors hover:border-brand-400 hover:text-brand-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
      >
        <Building2 className="size-4 shrink-0 text-slate-400" aria-hidden />
        <span className="truncate">{label}</span>
        <ChevronDown
          className={cn("size-4 shrink-0 transition-transform duration-150", open && "rotate-180")}
          aria-hidden
        />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-50 mt-1 max-h-96 w-64 overflow-auto rounded-xl border border-slate-200 bg-white p-1 shadow-lg"
        >
          <p className="px-3 pb-1 pt-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
            Le tue aziende
          </p>
          {companies.length === 0 ? (
            <p className="px-3 py-2 text-sm text-slate-500">Nessuna azienda: creane una.</p>
          ) : (
            companies.map((c) => {
              const isActive = c.id === activeCompanyId;
              return (
                <button
                  key={c.id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={isActive}
                  onClick={() => {
                    setActiveCompany(c.id);
                    setOpen(false);
                  }}
                  className={cn(
                    "flex w-full cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors",
                    isActive
                      ? "bg-brand-50 text-brand-700"
                      : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
                  )}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{c.ragione_sociale}</span>
                    <span className="block truncate text-xs text-slate-400">
                      P.IVA {c.partita_iva}
                    </span>
                  </span>
                  {isActive && <Check className="size-4 shrink-0 text-brand-600" aria-hidden />}
                </button>
              );
            })
          )}
          <div className="my-1 border-t border-slate-100" />
          <Link
            to="/app/aziende"
            role="menuitem"
            onClick={() => setOpen(false)}
            className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900"
          >
            <Settings2 className="size-4 text-slate-400" aria-hidden />
            Gestisci aziende
          </Link>
        </div>
      )}
    </div>
  );
}
