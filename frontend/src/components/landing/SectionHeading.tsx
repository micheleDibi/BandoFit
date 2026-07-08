import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

/** Intestazione di sezione riutilizzabile nella landing: eyebrow opzionale +
 *  titolo (h2, Sora) + sottotitolo. Centrata di default. */
export function SectionHeading({
  eyebrow,
  title,
  subtitle,
  align = "center",
  className,
}: {
  eyebrow?: string;
  title: ReactNode;
  subtitle?: ReactNode;
  align?: "center" | "left";
  className?: string;
}) {
  return (
    <div
      className={cn(
        "max-w-2xl",
        align === "center" ? "mx-auto text-center" : "text-left",
        className,
      )}
    >
      {eyebrow && (
        <p className="text-xs font-semibold uppercase tracking-wide text-brand-600">{eyebrow}</p>
      )}
      <h2 className="mt-2 font-display text-3xl font-bold tracking-tight text-slate-900">{title}</h2>
      {subtitle && <p className="mt-3 text-base leading-relaxed text-slate-600">{subtitle}</p>}
    </div>
  );
}
