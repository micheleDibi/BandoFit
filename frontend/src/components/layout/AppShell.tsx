import { LogOut, Menu, ShieldCheck, User, X } from "lucide-react";
import { useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";
import { useMe } from "../../hooks/useMe";
import { cn } from "../../lib/cn";
import { InviteBanner } from "../shared/InviteBanner";
import { PoweredBy } from "../shared/PoweredBy";
import { Logo } from "./Logo";

const navLinkClasses = ({ isActive }: { isActive: boolean }) =>
  cn(
    "rounded-lg px-3 py-2 text-sm font-medium transition-colors duration-150",
    isActive
      ? "bg-brand-50 text-brand-700"
      : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
  );

export function AppShell() {
  const { data: me } = useMe();
  const { signOut } = useAuth();
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);
  const isAdmin = me?.profile.role === "admin";

  const handleSignOut = async () => {
    await signOut();
    navigate("/");
  };

  const navLinks = (
    <>
      <NavLink to="/app/bandi" className={navLinkClasses} onClick={() => setMobileOpen(false)}>
        Bandi
      </NavLink>
      <NavLink to="/app/azienda" className={navLinkClasses} onClick={() => setMobileOpen(false)}>
        Azienda
      </NavLink>
      <NavLink to="/app/preferenze" className={navLinkClasses} onClick={() => setMobileOpen(false)}>
        Preferenze
      </NavLink>
      {isAdmin && (
        <>
          <NavLink
            to="/app/admin/utenti"
            className={navLinkClasses}
            onClick={() => setMobileOpen(false)}
          >
            Utenti
          </NavLink>
          <NavLink
            to="/app/admin/piani"
            className={navLinkClasses}
            onClick={() => setMobileOpen(false)}
          >
            Abbonamenti
          </NavLink>
        </>
      )}
    </>
  );

  return (
    <div className="flex min-h-dvh flex-col bg-surface">
      <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-7xl items-center gap-4 px-4 sm:px-6">
          <Link
            to="/app/bandi"
            className="rounded-lg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
            aria-label="BandoFit — vai all'elenco bandi"
          >
            <Logo />
          </Link>

          <nav className="ml-4 hidden items-center gap-1 md:flex" aria-label="Navigazione principale">
            {navLinks}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            {isAdmin && (
              <span className="hidden items-center gap-1 rounded-full bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-700 sm:inline-flex">
                <ShieldCheck className="size-3.5" aria-hidden />
                Admin
              </span>
            )}
            <Link
              to="/app/profilo"
              aria-label="Vai al tuo profilo"
              className="inline-flex h-9 cursor-pointer items-center gap-2 rounded-lg px-3 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-2 focus-visible:outline-brand-500"
            >
              <User className="size-4" aria-hidden />
              <span className="hidden sm:inline">
                {me?.profile.nome ?? me?.profile.email ?? "Profilo"}
              </span>
            </Link>
            <button
              type="button"
              onClick={handleSignOut}
              aria-label="Esci dall'account"
              className="inline-flex h-9 cursor-pointer items-center gap-2 rounded-lg px-3 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-2 focus-visible:outline-brand-500"
            >
              <LogOut className="size-4" aria-hidden />
              <span className="hidden sm:inline">Esci</span>
            </button>
            <button
              type="button"
              className="inline-flex size-9 cursor-pointer items-center justify-center rounded-lg text-slate-600 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-brand-500 md:hidden"
              onClick={() => setMobileOpen((v) => !v)}
              aria-expanded={mobileOpen}
              aria-label={mobileOpen ? "Chiudi menu" : "Apri menu"}
            >
              {mobileOpen ? <X className="size-5" aria-hidden /> : <Menu className="size-5" aria-hidden />}
            </button>
          </div>
        </div>
        {mobileOpen && (
          <nav
            className="flex flex-col gap-1 border-t border-slate-200 px-4 py-3 md:hidden"
            aria-label="Navigazione mobile"
          >
            {navLinks}
          </nav>
        )}
      </header>

      <InviteBanner />

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
        <Outlet />
      </main>

      <footer className="mt-auto border-t border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-2 px-4 py-4 sm:px-6">
          <p className="text-xs text-slate-400">
            © {new Date().getFullYear()} BandoFit
          </p>
          <PoweredBy />
        </div>
      </footer>
    </div>
  );
}
