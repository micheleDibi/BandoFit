import { Menu, ShieldCheck, X } from "lucide-react";
import { useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useActiveCompany } from "../../hooks/useActiveCompany";
import { useAuth } from "../../hooks/useAuth";
import { useMe } from "../../hooks/useMe";
import { cn } from "../../lib/cn";
import { hasAreaProgettista } from "../../lib/roles";
import { InviteBanner } from "../shared/InviteBanner";
import { PoweredBy } from "../shared/PoweredBy";
import { UpgradeBanner } from "../shared/UpgradeBanner";
import { CompanyMenu } from "./CompanyMenu";
import { Logo } from "./Logo";
import { NavMenu, type NavItem } from "./NavMenu";
import { NotificationBell } from "./NotificationBell";
import { UserMenu } from "./UserMenu";

// Link di navigazione principali (uso frequente): i bandi e le Consulenze,
// che è una feature a sé (aiuto umano sui bandi) e merita di essere in vista.
const directLinks: NavItem[] = [
  { to: "/app/bandi", label: "Bandi" },
  { to: "/app/salvati", label: "Salvati" },
  { to: "/app/calendario", label: "Calendario" },
  { to: "/app/ai-check", label: "AI-check" },
  { to: "/app/consulenze", label: "Consulenze" },
];

// Voci di ACCOUNT (te + fatturazione): vivono nel menu avatar (UserMenu). I dati
// azienda e la gestione portafoglio stanno nel CompanyMenu, non qui. La voce
// «Account collegati» (famiglia) è aggiunta condizionatamente in AppShell.
const accountBase: NavItem[] = [
  { to: "/app/preferenze", label: "Preferenze" },
  { to: "/app/abbonamento", label: "Abbonamento" },
  { to: "/app/addon", label: "I miei addon" },
  { to: "/app/fatturazione", label: "Dati di fatturazione" },
  { to: "/app/acquisti", label: "I tuoi acquisti" },
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
  { to: "/app/admin/pagamenti", label: "Pagamenti" },
];

const navLinkClasses = ({ isActive }: { isActive: boolean }) =>
  cn(
    "whitespace-nowrap rounded-lg px-2.5 py-2 text-sm font-medium transition-colors duration-150",
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
  // «Account collegati» (gestione famiglia) solo al titolare con posti collegati
  // e NON Advisor: in v1 Advisor e famiglia sono mutuamente esclusivi.
  const isParent = me?.family?.role === "parent";
  const accountLinks: NavItem[] =
    isParent && !isMulti
      ? [...accountBase, { to: "/app/profilo#collegati", label: "Account collegati" }]
      : accountBase;

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

          {/* Solo navigazione: 5 link diretti + i menu dei ruoli. L'azienda vive
              nel CompanyMenu e l'account (profilo/preferenze/abbonamento/uscita)
              nell'UserMenu, a destra. La nav per esteso entra da lg, sotto
              resta l'hamburger. */}
          <nav
            className="ml-3 hidden items-center gap-0.5 lg:flex"
            aria-label="Navigazione principale"
          >
            {directLinks.map((item) => (
              <NavLink key={item.to} to={item.to} className={navLinkClasses}>
                {item.label}
              </NavLink>
            ))}
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
            <CompanyMenu />
            <NotificationBell />
            <UserMenu
              nome={me?.profile.nome}
              email={me?.profile.email}
              items={accountLinks}
              onSignOut={handleSignOut}
            />
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
      <UpgradeBanner />

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
