import type { LucideIcon } from "lucide-react";
import { NavLink } from "react-router-dom";
import { cn } from "../../lib/cn";
import type { Area } from "../ui/area";
import { IconChip } from "../ui/IconChip";

/** Voce di menu senza icona (menu account). */
export interface VoceNav {
  to: string;
  label: string;
}

export interface NavItemProps extends VoceNav {
  icon: LucideIcon;
  /** L'area della voce (mappa in docs/design-system.md): colore dell'icona e
   *  della barretta della voce corrente. */
  area: Area;
  /** Solo la rotta esatta: la Home (`/app`) non deve restare accesa su /app/bandi. */
  end?: boolean;
  /** Contatore a destra (pillola corallo); `contatoreFrase` lo dice in parole. */
  contatore?: number;
  contatoreFrase?: string;
  /** Nome completo per le tecnologie assistive, se diverso dall'etichetta. */
  ariaLabel?: string;
  onNavigate?: () => void;
}

// La barretta a sinistra della voce corrente, nel colore dell'area. Sul navy il
// base di home (navy-800), account e admin (grigi scuri) non si vedrebbe: lì si
// usa la tinta chiara della stessa area. Classi scritte per intero.
const barretta: Record<Area, string> = {
  home: "bg-navy-200",
  bandi: "bg-area-bandi",
  aicheck: "bg-area-aicheck",
  partenariati: "bg-area-partenariati",
  consulenze: "bg-area-consulenze",
  scadenze: "bg-area-scadenze",
  azienda: "bg-area-azienda",
  account: "bg-area-account-soft",
  admin: "bg-area-admin-soft",
};

/** Voce della barra laterale navy: icona in un chip nel colore della sua area
 *  (fondo soft, icona ink: leggibile sul navy), parola in bianco/80, 36px di
 *  altezza. La voce corrente ha `aria-current="page"` (lo mette NavLink), fondo
 *  bianco/10, testo bianco in grassetto e la barretta dell'area a sinistra; al
 *  passaggio il fondo è bianco/5. Anello del focus bianco (sul navy l'accent
 *  non si vedrebbe). */
export function NavItem({
  to,
  label,
  icon,
  area,
  end,
  contatore = 0,
  contatoreFrase,
  ariaLabel,
  onNavigate,
}: NavItemProps) {
  return (
    <NavLink
      to={to}
      end={end}
      onClick={onNavigate}
      aria-label={ariaLabel}
      className={({ isActive }) =>
        cn(
          "relative flex h-9 items-center gap-2.5 rounded-control pr-3 pl-1.5 text-body font-medium",
          "transition-colors duration-150 ease-uscita",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white",
          isActive
            ? "bg-white/10 font-semibold text-white"
            : "text-white/80 hover:bg-white/5 hover:text-white",
        )
      }
    >
      {({ isActive }) => (
        <>
          {isActive && (
            <span
              aria-hidden
              className={cn("absolute inset-y-1.5 -left-3 w-1 rounded-r-pill", barretta[area])}
            />
          )}
          <IconChip icon={icon} area={area} size="sm" className="size-7" />
          <span className="min-w-0 flex-1 truncate">{label}</span>
          {contatore > 0 && (
            <>
              <span
                aria-hidden
                className="inline-flex h-5 min-w-5 items-center justify-center rounded-pill bg-warm px-1.5 text-caption font-semibold text-navy-950 tabular-nums"
              >
                {contatore > 99 ? "99+" : contatore}
              </span>
              {contatoreFrase && <span className="sr-only">, {contatoreFrase}</span>}
            </>
          )}
        </>
      )}
    </NavLink>
  );
}
