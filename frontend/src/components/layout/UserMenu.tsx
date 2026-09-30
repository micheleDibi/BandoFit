import { ChevronsUpDown, LogOut } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { NavLink } from "react-router-dom";
import { cn } from "../../lib/cn";
import { Avatar } from "../ui/Avatar";
import { Popover, usePopover } from "../ui/Popover";
import type { VoceNav } from "./NavItem";
import { spostaFocusVoce, voceMenu } from "./tastieraMenu";

/** Pulsante del menu account: avatar con le iniziali, nome e doppia freccia.
 *  `forwardRef` e props passate al `<button>`: è il trigger di `Popover`. */
const TriggerAccount = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { nome: string }
>(({ nome, className, ...props }, ref) => (
  <button
    ref={ref}
    type="button"
    aria-label={`Account: ${nome}. Apri il menu`}
    className={cn(
      "flex h-11 w-full cursor-pointer items-center gap-2.5 rounded-control px-2 text-left text-body font-medium text-ink",
      "transition-colors duration-150 hover:bg-sunken aria-expanded:bg-sunken",
      className,
    )}
    {...props}
  >
    <span aria-hidden>
      <Avatar nome={nome} />
    </span>
    <span className="min-w-0 flex-1 truncate">{nome}</span>
    <ChevronsUpDown className="size-4 shrink-0 text-ink-3" aria-hidden />
  </button>
));
TriggerAccount.displayName = "TriggerAccount";

function VociAccount({
  secondary,
  items,
  onSignOut,
  onNavigate,
}: {
  secondary: string | null;
  items: VoceNav[];
  onSignOut: () => void;
  onNavigate?: () => void;
}) {
  const { chiudi } = usePopover();
  return (
    <div
      role="menu"
      aria-label="Account"
      onKeyDown={spostaFocusVoce}
      className="flex flex-col gap-0.5"
    >
      {secondary && <p className="truncate px-2.5 py-1.5 text-small text-ink-3">{secondary}</p>}
      {items.map((it) => (
        <NavLink
          key={it.to}
          to={it.to}
          role="menuitem"
          onClick={() => {
            chiudi();
            onNavigate?.();
          }}
          className={({ isActive }) =>
            cn(voceMenu, isActive ? "bg-desk text-ink" : "text-ink hover:bg-desk")
          }
        >
          {it.label}
        </NavLink>
      ))}
      <div role="separator" className="my-1 border-t border-line" />
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          chiudi();
          onSignOut();
        }}
        className={cn(voceMenu, "w-full cursor-pointer text-ink hover:bg-desk")}
      >
        <LogOut className="size-4 text-ink-3" aria-hidden />
        Esci
      </button>
    </div>
  );
}

/** Menu «account» in fondo alla barra laterale: Profilo, Abbonamento, Account
 *  collegati (se titolare) ed Esci. Costruito su `Popover`: si apre verso
 *  l'alto quando sotto non c'è spazio, Esc e clic fuori lo chiudono, il focus
 *  torna al pulsante; frecce, Home ed End scorrono le voci. */
export function UserMenu({
  nome,
  email,
  items,
  onSignOut,
  onNavigate,
}: {
  nome?: string | null;
  email?: string | null;
  items: VoceNav[];
  onSignOut: () => void;
  onNavigate?: () => void;
}) {
  const displayName = nome?.trim() || email?.trim() || "Profilo";
  // Riga secondaria solo se abbiamo un nome: altrimenti duplicheremmo l'email.
  const secondary = nome?.trim() && email?.trim() ? email : null;

  return (
    <Popover trigger={<TriggerAccount nome={displayName} />} label="Menu account">
      <VociAccount
        secondary={secondary}
        items={items}
        onSignOut={onSignOut}
        onNavigate={onNavigate}
      />
    </Popover>
  );
}
