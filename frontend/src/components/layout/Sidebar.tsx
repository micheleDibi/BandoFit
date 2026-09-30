import {
  Bookmark,
  Building2,
  CalendarDays,
  ClipboardList,
  CreditCard,
  FileText,
  House,
  Layers,
  MessageSquare,
  Package,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  UserCog,
  Users,
  type LucideIcon,
} from "lucide-react";
import { useId, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";
import { useFunzioni } from "../../hooks/useFunzioni";
import { useMe } from "../../hooks/useMe";
import { useRiepilogoPartenariati } from "../../hooks/usePartenariati";
import { hasAreaProgettista } from "../../lib/roles";
import type { RiepilogoPartenariati } from "../../types";
import { CompanyMenu } from "./CompanyMenu";
import { Logo } from "./Logo";
import { NavItem, type VoceNav } from "./NavItem";
import { NotificationBell } from "./NotificationBell";
import { UserMenu } from "./UserMenu";

interface Voce extends VoceNav {
  icon: LucideIcon;
  end?: boolean;
}

// Le etichette sono i titoli delle pagine: stesso nome nel menu e nell'h1.
const vociBase: Voce[] = [
  { to: "/app", label: "Home", icon: House, end: true },
  { to: "/app/bandi", label: "Bandi", icon: FileText },
  { to: "/app/salvati", label: "Bandi salvati", icon: Bookmark },
  { to: "/app/calendario", label: "Calendario", icon: CalendarDays },
  { to: "/app/ai-check", label: "AI-check", icon: Sparkles },
];
const voceConsulenze: Voce = { to: "/app/consulenze", label: "Consulenze", icon: MessageSquare };
const vociAzienda: Voce[] = [
  { to: "/app/azienda", label: "Dati azienda", icon: Building2 },
  { to: "/app/preferenze", label: "Preferenze", icon: SlidersHorizontal },
];
// Per progettisti e admin (parità completa). Le disponibilità si gestiscono
// dal Calendario, non da una pagina dedicata.
const vociProgettista: Voce[] = [
  { to: "/app/progettista/richieste", label: "Richieste di consulenza", icon: ClipboardList },
];
const vociAdmin: Voce[] = [
  { to: "/app/admin/utenti", label: "Utenti", icon: UserCog },
  { to: "/app/admin/piani", label: "Piani", icon: Layers },
  { to: "/app/admin/addon", label: "Add-on", icon: Package },
  { to: "/app/admin/pagamenti", label: "Pagamenti", icon: CreditCard },
];
// Solo a modulo acceso (WP9): moderazione, verifiche, call, metriche.
const voceAdminPartenariati: Voce = {
  to: "/app/admin/partenariati",
  label: "Partenariati",
  icon: ShieldCheck,
};
// Menu account: ciò che è personale. «Account collegati» è aggiunto per i titolari.
const vociAccount: VoceNav[] = [
  { to: "/app/profilo", label: "Profilo" },
  { to: "/app/abbonamento", label: "Abbonamento" },
];

/** Frase del contatore per i lettori di schermo: le cose da fare (messaggi
 *  non letti, inviti e candidature da decidere, dal WP7) oppure, se non ce
 *  ne sono, le call «Per te» pubblicate negli ultimi 7 giorni. */
function fraseContatore(r: RiepilogoPartenariati | undefined): { numero: number; frase: string } {
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
    return { numero: daFare, frase: parti.join(", ") };
  }
  const nuove = r?.per_te_nuove ?? 0;
  return {
    numero: nuove,
    frase:
      nuove === 1
        ? "1 call per te pubblicata negli ultimi 7 giorni"
        : `${nuove} call per te pubblicate negli ultimi 7 giorni`,
  };
}

/** «Partenariati» con il contatore dal riepilogo (non è un contatore di non
 *  lette: le call «Per te» scendono da sole quando escono dalla settimana). */
function PartenariatiNavItem({ onNavigate }: { onNavigate?: () => void }) {
  const { data: riepilogo } = useRiepilogoPartenariati();
  const { numero, frase } = fraseContatore(riepilogo);
  return (
    <NavItem
      to="/app/partenariati"
      label="Partenariati"
      icon={Users}
      contatore={numero}
      contatoreFrase={frase}
      onNavigate={onNavigate}
    />
  );
}

function NavGroup({ label, children }: { label?: string; children: ReactNode }) {
  const id = useId();
  return (
    <div role="group" aria-labelledby={label ? id : undefined} className="flex flex-col gap-0.5">
      {label && (
        <p id={id} className="px-3 pb-1.5 text-caption text-ink-3">
          {label}
        </p>
      )}
      {children}
    </div>
  );
}

/** Contenuto della barra laterale (tavola `Main`): logo, selettore
 *  dell'azienda, gruppi di voci per ruolo e, in fondo, Notifiche e account.
 *  Lo stesso contenuto va nel cassetto mobile (`conLogo={false}`: il logo sta
 *  nella testata del cassetto), con `onNavigate` per chiuderlo. */
export function Sidebar({
  conLogo = true,
  onNavigate,
}: {
  conLogo?: boolean;
  onNavigate?: () => void;
}) {
  const { data: me } = useMe();
  const { partenariatiAttivo } = useFunzioni();
  const { signOut } = useAuth();
  const navigate = useNavigate();
  const isAdmin = me?.profile.role === "admin";
  const isProgettista = hasAreaProgettista(me?.profile.role);
  // «Account collegati»: per OGNI titolare con posti collegati (dal WP7/WP8
  // anche gli Advisor multi-azienda gestiscono membri).
  const isParent = me?.family?.role === "parent";
  const vociAdminTutte = partenariatiAttivo ? [...vociAdmin, voceAdminPartenariati] : vociAdmin;
  const vociAccountTutte: VoceNav[] = isParent
    ? [...vociAccount, { to: "/app/collegati", label: "Account collegati" }]
    : vociAccount;

  const handleSignOut = async () => {
    await signOut();
    navigate("/");
  };

  const voce = (v: Voce) => <NavItem key={v.to} {...v} onNavigate={onNavigate} />;

  return (
    <div className="flex min-h-full flex-1 flex-col gap-5">
      {conLogo && (
        <Link
          to="/app"
          onClick={onNavigate}
          aria-label="BandoFit — vai alla Home"
          className="self-start rounded-mark px-3"
        >
          <Logo variant="stack" />
        </Link>
      )}
      <CompanyMenu onNavigate={onNavigate} />
      <nav aria-label="Navigazione principale" className="flex flex-col gap-5">
        <NavGroup>{vociBase.map(voce)}</NavGroup>
        <NavGroup label="Servizi">
          {partenariatiAttivo && <PartenariatiNavItem onNavigate={onNavigate} />}
          {voce(voceConsulenze)}
        </NavGroup>
        <NavGroup label="La tua azienda">{vociAzienda.map(voce)}</NavGroup>
        {isProgettista && <NavGroup label="Progettista">{vociProgettista.map(voce)}</NavGroup>}
        {isAdmin && <NavGroup label="Amministrazione">{vociAdminTutte.map(voce)}</NavGroup>}
      </nav>
      <div className="mt-auto flex flex-col gap-1 pt-5">
        <NotificationBell variante="voce" onNavigate={onNavigate} />
        <UserMenu
          nome={me?.profile.nome}
          email={me?.profile.email}
          items={vociAccountTutte}
          onSignOut={handleSignOut}
          onNavigate={onNavigate}
        />
      </div>
    </div>
  );
}
