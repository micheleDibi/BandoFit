import { cn } from "../../lib/cn";
import { areaClassi, type Area } from "./area";

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

// I colori delle aree più vivaci (fondo soft, iniziali ink: ≥ 5,1:1), fra cui
// scegliere a partire dal nome.
const PALETTE: readonly Area[] = ["bandi", "aicheck", "partenariati", "consulenze", "scadenze", "azienda"];

/** Colore dell'avatar dal nome: deterministico (lo stesso nome ha sempre lo
 *  stesso colore, in ogni pagina e a ogni visita), senza significato. */
export function coloreAvatar(nome: string): string {
  let somma = 0;
  for (const carattere of nome.trim().toLowerCase()) {
    somma = (somma * 31 + (carattere.codePointAt(0) ?? 0)) % 2_147_483_647;
  }
  return areaClassi(PALETTE[somma % PALETTE.length]).suSoft;
}

/** Cerchio con le iniziali su un colore derivato dal nome. Non è un pulsante:
 *  chi lo rende cliccabile lo avvolge in un `<button>` con il suo `aria-label`. */
export function Avatar({ nome, size = "md", className }: AvatarProps) {
  return (
    <span
      role="img"
      aria-label={nome}
      className={cn(
        "inline-flex shrink-0 select-none items-center justify-center rounded-pill font-semibold",
        coloreAvatar(nome),
        taglie[size],
        className,
      )}
    >
      {iniziali(nome)}
    </span>
  );
}
