import { Menu, ShieldCheck, X } from "lucide-react";
import { useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";
import { useFunzioni } from "../../hooks/useFunzioni";
import { useMe } from "../../hooks/useMe";
import { useRiepilogoPartenariati } from "../../hooks/usePartenariati";
import { cn } from "../../lib/cn";
import { hasAreaProgettista } from "../../lib/roles";
import type { RiepilogoPartenariati } from "../../types";
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

// Modulo partenariati: link diretto, solo a modulo acceso (a modulo spento la
// pagina «non esiste», come le rotte del backend), con il badge delle novità.
const partenariatiLink: NavItem = { to: "/app/partenariati", label: "Partenariati" };

/** Frase del badge per i lettori di schermo e il `title`: le cose da fare
 *  (messaggi non letti, inviti e candidature da decidere, dal WP7) oppure,
 *  se non ce ne sono, le call «Per te» pubblicate negli ultimi 7 giorni. */
function fraseBadge(r: RiepilogoPartenariati | undefined): {
  numero: number;
  frase: string;
  daFare: boolean;
} {
  const messaggi = r?.messaggi_non_letti ?? 0;
  const inviti = r?.inviti_ricevuti ?? 0;
  const candidature = r?.candidature_da_decidere ?? 0;
  const daFare = messaggi + inviti + candidature;
  if (daFare > 0) {
    const parti = [
      messaggi ? (messaggi === 1 ? "1 messaggio non letto" : `${messaggi} messaggi non letti`) : null,
      inviti ? (inviti === 1 ? "1 invito da decidere" : `${inviti} inviti da decidere`) : null,
      candidature
        ? candidature === 1
          ? "1 candidatura da decidere"
          : `${candidature} candidature da decidere`
        : null,
    ].filter(Boolean);
    return { numero: daFare, frase: parti.join(", "), daFare: true };
  }
  const nuove = r?.per_te_nuove ?? 0;
  return {
    numero: nuove,
    frase:
      nuove === 1
        ? "1 call per te pubblicata negli ultimi 7 giorni"
        : `${nuove} call per te pubblicate negli ultimi 7 giorni`,
    daFare: false,
  };
}

/** Link «Partenariati» con un numero (dal riepilogo): le cose da fare
 *  (messaggi non letti, inviti e candidature da decidere) con un badge
 *  rosso; se non ce ne sono, le call «Per te» pubblicate negli ultimi 7
 *  giorni (non è un contatore di non lette: scende da solo quando una call
 *  esce dalla settimana). Numero visibile e, per i lettori di schermo, in
 *  parole. */
function PartenariatiNavLink({
  className,
  onClick,
}: {
  className: (stato: { isActive: boolean }) => string;
  onClick?: () => void;
}) {
  const { data: riepilogo } = useRiepilogoPartenariati();
  const { numero, frase, daFare } = fraseBadge(riepilogo);
  return (
    <NavLink to={partenariatiLink.to} className={className} onClick={onClick}>
      {partenariatiLink.label}
      {numero > 0 && (
        <>
          <span
            className={cn(
              "ml-1.5 inline-flex min-w-5 items-center justify-center rounded-full px-1.5 text-xs font-semibold text-white tabular",
              daFare ? "bg-red-600" : "bg-brand-500",
            )}
            title={frase}
            aria-hidden
          >
            {numero > 99 ? "99+" : numero}
          </span>
          <span className="sr-only">, {frase}</span>
        </>
      )}
    </NavLink>
  );
}

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
// Solo a modulo acceso (WP9): moderazione, verifiche, call, metriche.
const adminPartenariatiLink: NavItem = { to: "/app/admin/partenariati", label: "Partenariati" };

const navLinkClasses = ({ isActive }: { isActive: boolean }) =>
  cn(
    "whitespace-nowrap rounded-lg px-2.5 py-2 text-sm font-medium transition-colors duration-150",
    isActive
      ? "bg-brand-50 text-brand-700"
      : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
  );

export function AppShell() {
  const { data: me } = useMe();
  const { partenariatiAttivo } = useFunzioni();
  const { signOut } = useAuth();
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);
  const isAdmin = me?.profile.role === "admin";
  const isProgettista = hasAreaProgettista(me?.profile.role);
  // «Account collegati»: per OGNI titolare con posti collegati (dal WP7/WP8
  // anche gli Advisor multi-azienda gestiscono membri, con appartenenza e
  // visibilità per azienda). La pagina dedicata è /app/collegati.
  const isParent = me?.family?.role === "parent";
  const linkAdmin = partenariatiAttivo ? [...adminLinks, adminPartenariatiLink] : adminLinks;
  const accountLinks: NavItem[] = isParent
    ? [...accountBase, { to: "/app/collegati", label: "Account collegati" }]
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

          {/* Solo navigazione: 5 link diretti (6 con i partenariati, con il
              badge delle novità) + i menu dei ruoli. L'azienda vive
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
            {partenariatiAttivo && <PartenariatiNavLink className={navLinkClasses} />}
            {isProgettista && (
              <NavMenu label="Progettista" items={progettistaLinks} />
            )}
            {isAdmin && (
              <NavMenu
                label="Admin"
                items={linkAdmin}
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
            {partenariatiAttivo && (
              <PartenariatiNavLink className={navLinkClasses} onClick={() => setMobileOpen(false)} />
            )}
            {isProgettista && (
              <>
                <p className={mobileSectionLabel}>Progettista</p>
                {progettistaLinks.map(mobileLink)}
              </>
            )}
            {isAdmin && (
              <>
                <p className={mobileSectionLabel}>Amministrazione</p>
                {linkAdmin.map(mobileLink)}
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
