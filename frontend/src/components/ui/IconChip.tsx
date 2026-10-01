import type { LucideIcon } from "lucide-react";
import { isValidElement, type ReactElement } from "react";
import { cn } from "../../lib/cn";
import { areaClassi, type Area } from "./area";

export type IconChipSize = "sm" | "md" | "lg";

/** L'icona di un chip: il componente di lucide (`FileText`, la forma preferita)
 *  o un elemento già creato (`<FileText />`), che il chip ridimensiona. */
export type IconaChip = LucideIcon | ReactElement;

export interface IconChipProps {
  icon: IconaChip;
  /** Colore dell'area; default `home` (il navy del marchio). */
  area?: Area;
  /** `sm` 32px (intestazione dei pannelli), `md` 40px (card, KPI), `lg` 48px (fascia, stato vuoto). */
  size?: IconChipSize;
  /** Su fondo scuro (la fascia `bg-banda`): bianco al 15% e icona bianca. */
  inverse?: boolean;
  className?: string;
}

const taglie: Record<IconChipSize, { chip: string; icona: string; elemento: string }> = {
  sm: { chip: "size-8 rounded-control", icona: "size-4", elemento: "[&_svg]:size-4" },
  md: { chip: "size-10 rounded-control", icona: "size-5", elemento: "[&_svg]:size-5" },
  lg: { chip: "size-12 rounded-panel", icona: "size-6", elemento: "[&_svg]:size-6" },
};

/** Icona in un quadrato arrotondato nel colore dell'area (fondo soft, icona
 *  ink). Sempre decorativa (`aria-hidden`): accanto c'è la parola che conta. */
export function IconChip({ icon, area = "home", size = "md", inverse = false, className }: IconChipProps) {
  const t = taglie[size];
  const classi = areaClassi(area);
  let figlio: ReactElement;
  if (isValidElement(icon)) {
    figlio = <span className={cn("contents", t.elemento)}>{icon}</span>;
  } else {
    const Icona = icon as LucideIcon;
    figlio = <Icona className={t.icona} />;
  }
  return (
    <span
      aria-hidden
      className={cn(
        "inline-flex shrink-0 items-center justify-center",
        t.chip,
        inverse ? "bg-white/15 text-white ring-1 ring-white/20 ring-inset" : cn(classi.soft, classi.ink),
        className,
      )}
    >
      {figlio}
    </span>
  );
}
