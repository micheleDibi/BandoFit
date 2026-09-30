import { Bell } from "lucide-react";
import { NavLink } from "react-router-dom";
import { useNotifications } from "../../hooks/useNotifications";
import { cn } from "../../lib/cn";
import { NOTIFICHE_COPY } from "../../lib/copy";
import { NavItem } from "./NavItem";

/** La voce «Notifiche»: nella barra laterale è una voce di menu con il
 *  contatore delle non lette, nella barra mobile è la campanella. Entrambe
 *  portano alla pagina delle notifiche (niente pannello a comparsa: la pagina
 *  è a un tocco); il conteggio viene dallo stesso hook di sempre. */
export function NotificationBell({
  variante = "voce",
  onNavigate,
}: {
  variante?: "voce" | "icona";
  onNavigate?: () => void;
}) {
  const { data } = useNotifications();
  const nonLette = data?.non_lette ?? 0;
  const nome = nonLette > 0 ? NOTIFICHE_COPY.apriConNonLette(nonLette) : NOTIFICHE_COPY.apri;

  if (variante === "voce") {
    return (
      <NavItem
        to="/app/notifiche"
        label="Notifiche"
        icon={Bell}
        contatore={nonLette}
        ariaLabel={nonLette > 0 ? nome : undefined}
        onNavigate={onNavigate}
      />
    );
  }

  return (
    <NavLink
      to="/app/notifiche"
      onClick={onNavigate}
      aria-label={nome}
      className={({ isActive }) =>
        cn(
          "relative inline-flex size-10 shrink-0 items-center justify-center rounded-control text-ink-2",
          "transition-colors duration-150 hover:bg-sunken hover:text-ink",
          isActive && "bg-sunken text-ink",
        )
      }
    >
      <Bell className="size-5" strokeWidth={1.75} aria-hidden />
      {nonLette > 0 && (
        <span
          aria-hidden
          className="absolute -right-0.5 -top-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-pill bg-accent px-1.5 text-caption font-semibold text-on-accent tabular-nums"
        >
          {nonLette > 99 ? "99+" : nonLette}
        </span>
      )}
    </NavLink>
  );
}
