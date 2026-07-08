import { ChevronDown } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { cn } from "../../lib/cn";

export interface NavItem {
  to: string;
  label: string;
}

/** Menu a tendina per la navbar desktop: raggruppa voci correlate sotto un
 *  unico trigger. Si chiude su selezione, click fuori ed Esc; il trigger è
 *  «attivo» quando la rotta corrente è una delle voci del gruppo. */
export function NavMenu({
  label,
  items,
  icon,
}: {
  label: string;
  items: NavItem[];
  icon?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const { pathname } = useLocation();
  const isActive = items.some((it) => pathname === it.to || pathname.startsWith(`${it.to}/`));

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

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        className={cn(
          "inline-flex cursor-pointer items-center gap-1 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500",
          isActive
            ? "bg-brand-50 text-brand-700"
            : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
        )}
      >
        {icon}
        {label}
        <ChevronDown
          className={cn("size-4 transition-transform duration-150", open && "rotate-180")}
          aria-hidden
        />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute left-0 top-full z-50 mt-1 min-w-44 rounded-xl border border-slate-200 bg-white p-1 shadow-lg"
        >
          {items.map((it) => (
            <NavLink
              key={it.to}
              to={it.to}
              role="menuitem"
              onClick={() => setOpen(false)}
              className={({ isActive: active }) =>
                cn(
                  "block rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                  active
                    ? "bg-brand-50 text-brand-700"
                    : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
                )
              }
            >
              {it.label}
            </NavLink>
          ))}
        </div>
      )}
    </div>
  );
}
