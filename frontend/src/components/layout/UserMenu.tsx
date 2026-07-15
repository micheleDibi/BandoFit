import { ChevronDown, LogOut } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, NavLink } from "react-router-dom";
import { cn } from "../../lib/cn";
import type { NavItem } from "./NavMenu";

/** Menu «account» della navbar: raccoglie sotto un solo avatar tutto ciò che è
 *  personale (profilo, impostazioni, uscita), così la barra in alto resta ai
 *  soli link di navigazione. Si chiude su selezione, click fuori ed Esc —
 *  stesso comportamento di NavMenu/CompanyMenu. */
export function UserMenu({
  nome,
  email,
  items,
  onSignOut,
}: {
  nome?: string | null;
  email?: string | null;
  items: NavItem[];
  onSignOut: () => void;
}) {
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

  const displayName = nome?.trim() || email?.trim() || "Profilo";
  // Riga secondaria solo se abbiamo un nome: altrimenti duplicheremmo l'email.
  const secondary = nome?.trim() && email?.trim() ? email : null;
  const initial = (nome?.trim() || email?.trim() || "?").charAt(0).toUpperCase();

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label="Apri menu account"
        className="inline-flex h-9 cursor-pointer items-center gap-2 rounded-lg py-1 pl-1 pr-2 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
      >
        <span className="inline-flex size-7 items-center justify-center rounded-full bg-brand-100 text-xs font-semibold text-brand-700">
          {initial}
        </span>
        <span className="hidden max-w-28 truncate xl:inline">{displayName}</span>
        <ChevronDown
          className={cn("size-4 shrink-0 transition-transform duration-150", open && "rotate-180")}
          aria-hidden
        />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-50 mt-1 w-60 rounded-xl border border-slate-200 bg-white p-1 shadow-lg"
        >
          <Link
            to="/app/profilo"
            role="menuitem"
            onClick={() => setOpen(false)}
            className="flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors hover:bg-slate-100"
          >
            <span className="inline-flex size-9 shrink-0 items-center justify-center rounded-full bg-brand-100 text-sm font-semibold text-brand-700">
              {initial}
            </span>
            <span className="min-w-0">
              <span className="block truncate text-sm font-medium text-slate-900">
                {displayName}
              </span>
              <span className="block truncate text-xs text-slate-400">
                {secondary ?? "Vai al profilo"}
              </span>
            </span>
          </Link>

          {items.length > 0 && (
            <>
              <div className="my-1 border-t border-slate-100" />
              {items.map((it) => (
                <NavLink
                  key={it.to}
                  to={it.to}
                  role="menuitem"
                  onClick={() => setOpen(false)}
                  className={({ isActive }) =>
                    cn(
                      "block rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                      isActive
                        ? "bg-brand-50 text-brand-700"
                        : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
                    )
                  }
                >
                  {it.label}
                </NavLink>
              ))}
            </>
          )}

          <div className="my-1 border-t border-slate-100" />
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onSignOut();
            }}
            className="flex w-full cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900"
          >
            <LogOut className="size-4 text-slate-400" aria-hidden />
            Esci
          </button>
        </div>
      )}
    </div>
  );
}
