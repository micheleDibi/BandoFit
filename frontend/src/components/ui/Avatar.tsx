import { cn } from "../../lib/cn";

export type AvatarSize = "sm" | "md";

export interface AvatarProps {
  /** Nome completo: l'avatar mostra le iniziali e lo annuncia per intero. */
  nome: string;
  /** `sm` 24px (righe compatte), `md` 32px (barra laterale, tabelle). */
  size?: AvatarSize;
  className?: string;
}

const taglie: Record<AvatarSize, string> = {
  sm: "size-6 text-caption",
  md: "size-8 text-small",
};

/** Iniziali di un nome: la prima lettera delle prime due parole
 *  («Giulia Rinaldi» → «GR», «Officine Rinaldi S.r.l.» → «OR», «Marta» → «M»). */
export function iniziali(nome: string): string {
  const parole = nome.trim().split(/\s+/).filter(Boolean);
  return parole
    .slice(0, 2)
    .map((parola) => parola[0] ?? "")
    .join("")
    .toUpperCase();
}

/** Cerchio con le iniziali su `accent-soft`: la sola pillola ammessa insieme a
 *  contatori e filtri attivi. Non è un pulsante: chi lo rende cliccabile lo
 *  avvolge in un `<button>` con il suo `aria-label`. */
export function Avatar({ nome, size = "md", className }: AvatarProps) {
  return (
    <span
      role="img"
      aria-label={nome}
      className={cn(
        "inline-flex shrink-0 select-none items-center justify-center rounded-pill",
        "bg-accent-soft font-semibold text-accent-hover",
        taglie[size],
        className,
      )}
    >
      {iniziali(nome)}
    </span>
  );
}
