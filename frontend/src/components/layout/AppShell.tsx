import { LogOut, Menu, ShieldCheck, User, X } from "lucide-react";
import { useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useActiveCompany } from "../../hooks/useActiveCompany";
import { useAuth } from "../../hooks/useAuth";
import { useMe } from "../../hooks/useMe";
import { cn } from "../../lib/cn";
import { hasAreaProgettista } from "../../lib/roles";
import { InviteBanner } from "../shared/InviteBanner";
import { PoweredBy } from "../shared/PoweredBy";
import { CompanySwitcher } from "./CompanySwitcher";
import { Logo } from "./Logo";
import { NavMenu, type NavItem } from "./NavMenu";
import { NotificationBell } from "./NotificationBell";

// Voci sempre dirette (le azioni più frequenti sui bandi).
const directLinks: NavItem[] = [
  { to: "/app/bandi", label: "Bandi" },
  { to: "/app/salvati", label: "Salvati" },
  { to: "/app/calendario", label: "Calendario" },
  { to: "/app/ai-check", label: "AI-check" },
];

// Raggruppate sotto «Impostazioni»: profilo aziendale e account. La voce
// «Aziende» (gestione multi-azienda) compare solo per gli Advisor.
const impostazioniBase: NavItem[] = [
  { to: "/app/azienda", label: "Azienda" },
  { to: "/app/consulenze", label: "Consulenze" },
  { to: "/app/preferenze", label: "Preferenze" },
  { to: "/app/abbonamento", label: "Abbonamento" },
];

// Raggruppate sotto «Progettista» (per progettisti e admin: parità completa).
// Le disponibilità si gestiscono dal Calendario, non da una pagina dedicata.
const progettistaLinks: NavItem[] = [
  { to: "/app/progettista/richieste", label: "Richieste" },
];

// Raggruppate sotto «Admin» (solo per gli amministratori).
const adminLinks: NavItem[] = [
  { to: "/app/admin/utenti", label: "Utenti" },
  { to: "/app/admin/piani", label: "Piani" },
  { to: "/app/admin/addon", label: "Add-on" },
];

const navLinkClasses = ({ isActive }: { isActive: boolean }) =>
  cn(
    "rounded-lg px-2.5 py-2 text-sm font-medium transition-colors duration-150",
    isActive
      ? "bg-brand-50 text-brand-700"
      : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
  );

export function AppShell() {
  const { data: me } = useMe();
  const { signOut } = useAuth();
  const { isMulti } = useActiveCompany();
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);
  const isAdmin = me?.profile.role === "admin";
  const isProgettista = hasAreaProgettista(me?.profile.role);
  const impostazioniLinks: NavItem[] = isMulti
    ? [{ to: "/app/aziende", label: "Aziende" }, ...impostazioniBase]
    : impostazioniBase;

  const handleSignOut = async () => {
    await signOut();
    navigate("/");
  };

  // Lista mobile (hamburger): gruppi come sezioni con intestazione.
  const mobileLink = (item: NavItem) => (
    <NavLink
      key={item.to}
      to={item.to}
      className={navLinkClasses}
      onClick={() => setMobileOpen(false)}
    >
      {item.label}
    </NavLink>
  );
  const mobileSectionLabel =
    "px-2.5 pt-3 pb-1 text-xs font-semibold uppercase tracking-wide text-slate-400";

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

          {/* 4 link diretti + 2 menu raggruppati: la nav per esteso entra da
              lg, sotto resta il menu hamburger. */}
          <nav
            className="ml-3 hidden items-center gap-0.5 lg:flex"
            aria-label="Navigazione principale"
          >
            {directLinks.map((item) => (
              <NavLink key={item.to} to={item.to} className={navLinkClasses}>
                {item.label}
              </NavLink>
            ))}
            <NavMenu label="Impostazioni" items={impostazioniLinks} />
            {isProgettista && (
              <NavMenu label="Progettista" items={progettistaLinks} />
            )}
            {isAdmin && (
              <NavMenu
                label="Admin"
                items={adminLinks}
                icon={<ShieldCheck className="size-3.5" aria-hidden />}
              />
            )}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <CompanySwitcher />
            <NotificationBell />
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
              className="inline-flex size-9 cursor-pointer items-center justify-center rounded-lg text-slate-600 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-brand-500 lg:hidden"
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
            className="flex flex-col gap-1 border-t border-slate-200 px-4 py-3 lg:hidden"
            aria-label="Navigazione mobile"
          >
            {directLinks.map(mobileLink)}
            <p className={mobileSectionLabel}>Impostazioni</p>
            {impostazioniLinks.map(mobileLink)}
            {isProgettista && (
              <>
                <p className={mobileSectionLabel}>Progettista</p>
                {progettistaLinks.map(mobileLink)}
              </>
            )}
            {isAdmin && (
              <>
                <p className={mobileSectionLabel}>Amministrazione</p>
                {adminLinks.map(mobileLink)}
              </>
            )}
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
