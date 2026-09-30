import { Check, CircleAlert, Info, TriangleAlert, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export type AlertTono = "info" | "ok" | "attenzione" | "errore";

export interface AlertProps {
  tono: AlertTono;
  /** Prima riga in grassetto; il testo va in `children`. */
  titolo?: string;
  children: ReactNode;
  /** Azione a destra (un `TextLink` o un `Button` piccolo). */
  azione?: ReactNode;
  className?: string;
}

const toni: Record<AlertTono, { classi: string; icona: LucideIcon; ruolo: "status" | "alert" }> = {
  info: {
    classi: "border-accent-line bg-accent-soft [&>svg]:text-accent",
    icona: Info,
    ruolo: "status",
  },
  ok: { classi: "border-fit bg-fit-soft [&>svg]:text-fit-ink", icona: Check, ruolo: "status" },
  attenzione: {
    classi: "border-warning-line bg-warning-soft [&>svg]:text-warning-ink",
    icona: TriangleAlert,
    ruolo: "alert",
  },
  errore: {
    classi: "border-danger-line bg-danger-soft [&>svg]:text-danger",
    icona: CircleAlert,
    ruolo: "alert",
  },
};

/** Avviso nella pagina: bordo intero, icona e parola, mai una barra colorata a
 *  sinistra. Info e ok sono `status` (annuncio educato); attenzione ed errore
 *  sono `alert` (annuncio immediato). */
export function Alert({ tono, titolo, children, azione, className }: AlertProps) {
  const { classi, icona: Icona, ruolo } = toni[tono];
  return (
    <div
      role={ruolo}
      className={cn(
        "flex items-start gap-3 rounded-control border px-4 py-3 text-body text-ink",
        classi,
        className,
      )}
    >
      <Icona className="mt-px size-5 shrink-0" aria-hidden />
      <div className="flex min-w-0 grow flex-col gap-0.5">
        {titolo && <p className="font-semibold">{titolo}</p>}
        <div>{children}</div>
      </div>
      {azione && <div className="ml-auto shrink-0 self-start">{azione}</div>}
    </div>
  );
}
