import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cn } from "../../lib/cn";

export type IconButtonSize = "sm" | "md";
export type IconButtonVariant = "quiet" | "secondary";

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  /** Nome dell'azione, obbligatorio: diventa `aria-label` (il pulsante è di sola icona). */
  label: string;
  /** Icona di lucide; la taglia la decide il pulsante (16px in `sm`, 20px in `md`). */
  icon: ReactNode;
  size?: IconButtonSize;
  variant?: IconButtonVariant;
}

const taglie: Record<IconButtonSize, string> = {
  sm: "size-8 [&_svg]:size-4",
  md: "size-10 [&_svg]:size-5",
};

const varianti: Record<IconButtonVariant, string> = {
  quiet: "text-ink-2 hover:bg-sunken hover:text-ink",
  secondary: "border border-line-control bg-sheet text-ink hover:bg-desk",
};

/** Pulsante di sola icona, 32/40px. Sempre con `aria-label`; per spiegare l'icona
 *  al passaggio del mouse lo si avvolge in `Tooltip`. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  ({ label, icon, size = "md", variant = "quiet", className, type = "button", ...props }, ref) => (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      className={cn(
        "inline-flex shrink-0 cursor-pointer items-center justify-center rounded-control",
        "transition-colors duration-150",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        "disabled:pointer-events-none disabled:text-ink-3",
        taglie[size],
        varianti[variant],
        className,
      )}
      {...props}
    >
      <span className="contents" aria-hidden>
        {icon}
      </span>
    </button>
  ),
);
IconButton.displayName = "IconButton";
