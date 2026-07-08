import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

/** Card di una funzionalità nella griglia della landing. La variante
 *  `featured` (usata per l'AI-check) risalta con gradiente chiaro brand e
 *  occupa due colonne solo da `lg` (a sm resta a una colonna: nessuna cella
 *  orfana su mobile/tablet). */
export function FeatureCard({
  icon: Icon,
  title,
  description,
  featured = false,
  children,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  featured?: boolean;
  children?: ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex flex-col rounded-xl border p-6 shadow-card transition-shadow hover:shadow-card-hover",
        featured
          ? "border-brand-200 bg-gradient-to-b from-brand-50/70 to-white lg:col-span-2"
          : "border-slate-200 bg-white",
      )}
    >
      <div
        className={cn(
          "inline-flex w-fit rounded-lg p-2.5",
          featured ? "bg-brand-100 text-brand-700" : "bg-brand-50 text-brand-600",
        )}
      >
        <Icon className="size-5" aria-hidden />
      </div>
      <h3
        className={cn(
          "mt-4 font-display font-semibold text-slate-900",
          featured ? "text-lg" : "text-base",
        )}
      >
        {title}
      </h3>
      <p className="mt-2 text-sm leading-relaxed text-slate-600">{description}</p>
      {children && <div className="mt-4">{children}</div>}
    </div>
  );
}
