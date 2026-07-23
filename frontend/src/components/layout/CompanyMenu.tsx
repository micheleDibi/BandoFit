import { Building2, Check, ChevronDown, FileText, Settings2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, NavLink } from "react-router-dom";
import { useActiveCompany } from "../../hooks/useActiveCompany";
import { useMe } from "../../hooks/useMe";
import { cn } from "../../lib/cn";

/** Menu «Azienda» della navbar: il punto unico di tutto ciò che riguarda
 *  l'azienda su cui operi.
 *  - non‑Advisor (una sola azienda): link diretto ai «Dati azienda».
 *  - Advisor (multi-azienda): dropdown con lo switch dell'azienda attiva +
 *    scorciatoie a «Dati azienda» e «Gestisci aziende» (il portafoglio).
 *  Chiusura su selezione / click fuori / Esc, come NavMenu/UserMenu. */
export function CompanyMenu() {
  const { isMulti, companies, activeCompanyId, setActiveCompany } = useActiveCompany();
  const { data: me } = useMe();
  // Un membro attivo naviga le aziende VISIBILI ma non gestisce il
  // portafoglio (endpoint owner-only): niente «Gestisci aziende».
  const isActiveChild = me?.family?.role === "child" && me.family.status === "active";
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

  // Non‑Advisor: una sola azienda, nessun portafoglio → link diretto ai dati
  // (nessuna dipendenza da `companies`, il cui endpoint è owner-only).
  if (!isMulti) {
    return (
      <NavLink
        to="/app/azienda"
        className={({ isActive }) =>
          cn(
            "inline-flex h-9 items-center gap-2 rounded-lg border px-3 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500",
            isActive
              ? "border-brand-400 bg-brand-50 text-brand-700"
              : "border-slate-300 text-slate-700 hover:border-brand-400 hover:text-brand-600",
          )
        }
      >
        <Building2 className="size-4 shrink-0 text-slate-400" aria-hidden />
        <span className="hidden sm:inline">Dati azienda</span>
      </NavLink>
    );
  }

  const active = companies.find((c) => c.id === activeCompanyId);
  const label = active?.ragione_sociale ?? "Azienda";

  const itemLink =
    "flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900";

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={`Azienda attiva: ${label}. Cambia azienda o apri i dati`}
        className="inline-flex h-9 cursor-pointer items-center gap-2 rounded-lg border border-slate-300 px-3 text-sm font-medium text-slate-700 transition-colors hover:border-brand-400 hover:text-brand-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
      >
        <Building2 className="size-4 shrink-0 text-slate-400" aria-hidden />
        <span className="hidden max-w-44 truncate sm:inline">{label}</span>
        <ChevronDown
          className={cn("size-4 shrink-0 transition-transform duration-150", open && "rotate-180")}
          aria-hidden
        />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-50 mt-1 w-64 rounded-xl border border-slate-200 bg-white p-1 shadow-lg"
        >
          <p className="px-3 pb-1 pt-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
            Le tue aziende
          </p>
          {companies.length === 0 ? (
            <p className="px-3 py-2 text-sm text-slate-500">Nessuna azienda: creane una.</p>
          ) : (
            <div className="max-h-72 overflow-auto">
              {companies.map((c) => {
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
              })}
            </div>
          )}
          <div className="my-1 border-t border-slate-100" />
          <Link to="/app/azienda" role="menuitem" onClick={() => setOpen(false)} className={itemLink}>
            <FileText className="size-4 text-slate-400" aria-hidden />
            Dati azienda
          </Link>
          {!isActiveChild && (
            <Link to="/app/aziende" role="menuitem" onClick={() => setOpen(false)} className={itemLink}>
              <Settings2 className="size-4 text-slate-400" aria-hidden />
              Gestisci aziende
            </Link>
          )}
        </div>
      )}
    </div>
  );
}
