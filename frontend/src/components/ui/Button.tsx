import { forwardRef, type ButtonHTMLAttributes } from "react";
import { Link, type LinkProps } from "react-router-dom";
import { cn } from "../../lib/cn";
import { Spinner } from "./Spinner";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "inverse";
type Size = "sm" | "md" | "lg";

/** Un solo pulsante pieno per schermata (`primary`, con un'ombra leggera); gli
 *  altri sono secondari (bordo `line-control`), testuali (`ghost`, il «quiet»
 *  delle tavole) o distruttivi (`danger`: bordo e testo in danger, mai pieno).
 *  `inverse` (fondo bianco, testo navy, anello del focus bianco) solo sulla
 *  fascia navy di `PageHeader`: lì fa la parte del primario. */
const variants: Record<Variant, string> = {
  primary: "bg-accent text-on-accent shadow-card hover:bg-accent-hover hover:shadow-card-hover",
  secondary: "border-line-control bg-sheet text-ink hover:border-ink-3 hover:bg-desk",
  ghost: "text-accent-hover hover:bg-accent-soft",
  danger: "border-danger bg-sheet text-danger hover:bg-danger-soft",
  inverse:
    "bg-sheet text-navy-900 shadow-card hover:bg-brand-50 hover:shadow-card-hover focus-visible:outline-white",
};

// `sm` cambia taglia con `text-small` (13/20): il peso 600 va ridetto, perché
// `text-small` sostituisce `text-title-group` nella merge.
const sizes: Record<Size, string> = {
  sm: "h-8 px-3 gap-2 text-small font-semibold",
  md: "h-10 px-4 gap-2",
  lg: "h-12 px-5 gap-2",
};

// Il testuale ha meno spazio ai lati (bf-btn-quiet): non ha un bordo da riempire.
const ghostSizes: Record<Size, string> = {
  sm: "px-2",
  md: "px-2.5",
  lg: "px-3",
};

export function buttonClasses(variant: Variant = "primary", size: Size = "md", className?: string) {
  return cn(
    "inline-flex cursor-pointer items-center justify-center whitespace-nowrap rounded-control",
    "border border-transparent text-title-group",
    // Colore, bordo e ombra in 150 ms; alla pressione si stringe appena (non con il movimento ridotto).
    "transition duration-150 ease-uscita motion-safe:active:scale-[0.98]",
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
    "disabled:pointer-events-none disabled:opacity-50",
    variants[variant],
    sizes[size],
    variant === "ghost" && ghostSizes[size],
    className,
  );
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = "primary", size = "md", loading, disabled, className, children, ...props }, ref) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={buttonClasses(variant, size, className)}
      {...props}
    >
      {loading && <Spinner size="sm" className="text-current" />}
      {children}
    </button>
  ),
);
Button.displayName = "Button";

/** Link con l'aspetto di un bottone: evita il pattern non valido
 *  <Link><Button>…</Button></Link> (elemento interattivo dentro interattivo). */
export interface LinkButtonProps extends LinkProps {
  variant?: Variant;
  size?: Size;
}

export function LinkButton({ variant = "primary", size = "md", className, ...props }: LinkButtonProps) {
  return <Link className={buttonClasses(variant, size, className)} {...props} />;
}
