import type { LucideIcon } from "lucide-react";
import { NavLink } from "react-router-dom";
import { cn } from "../../lib/cn";

/** Voce di menu senza icona (menu account). */
export interface VoceNav {
  to: string;
  label: string;
}

export interface NavItemProps extends VoceNav {
  icon: LucideIcon;
  /** Solo la rotta esatta: la Home (`/app`) non deve restare accesa su /app/bandi. */
  end?: boolean;
  /** Contatore a destra (pillola su `accent`); `contatoreFrase` lo dice in parole. */
  contatore?: number;
  contatoreFrase?: string;
  /** Nome completo per le tecnologie assistive, se diverso dall'etichetta. */
  ariaLabel?: string;
  onNavigate?: () => void;
}

/** Voce della barra laterale: icona 20px e parola, 36px di altezza. La voce
 *  corrente ha `aria-current="page"` (lo mette NavLink), fondo `sheet` e icona
 *  `accent`; al passaggio il fondo è `sunken`. */
export function NavItem({
  to,
  label,
  icon: Icon,
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
          "flex h-9 items-center gap-2.5 rounded-control px-3 text-body font-medium",
          "transition-colors duration-150",
          isActive
            ? "bg-sheet text-ink ring-1 ring-line ring-inset [&>svg]:text-accent"
            : "text-ink-2 hover:bg-sunken hover:text-ink",
        )
      }
    >
      <Icon className="size-5 shrink-0" strokeWidth={1.75} aria-hidden />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {contatore > 0 && (
        <>
          <span
            aria-hidden
            className="inline-flex h-5 min-w-5 items-center justify-center rounded-pill bg-accent px-1.5 text-caption font-semibold text-on-accent tabular-nums"
          >
            {contatore > 99 ? "99+" : contatore}
          </span>
          {contatoreFrase && <span className="sr-only">, {contatoreFrase}</span>}
        </>
      )}
    </NavLink>
  );
}
