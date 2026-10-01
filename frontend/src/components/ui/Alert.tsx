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
  /** Ruolo ARIA: di default `status` per info/ok e `alert` per attenzione/errore.
   *  `none` dentro una regione `aria-live` già montata, che annuncia da sé
   *  (due regioni annidate rischiano il doppio annuncio). */
  ruolo?: "status" | "alert" | "none";
  className?: string;
}

// Fondo soft del tono, filetto chiaro e bordo sinistro di 4px nel colore pieno,
// icona nel tono. Il testo resta `ink` (≥ 11:1 su ogni fondo).
const toni: Record<AlertTono, { classi: string; icona: LucideIcon; ruolo: "status" | "alert" }> = {
  info: {
    classi: "border-accent-line border-l-accent bg-accent-soft [&>svg]:text-accent",
    icona: Info,
    ruolo: "status",
  },
  ok: {
    classi: "border-fit/40 border-l-fit bg-fit-soft [&>svg]:text-fit-ink",
    icona: Check,
    ruolo: "status",
  },
  attenzione: {
    classi: "border-warning-line border-l-warning bg-warning-soft [&>svg]:text-warning-ink",
    icona: TriangleAlert,
    ruolo: "alert",
  },
  errore: {
    classi: "border-danger-line border-l-danger bg-danger-soft [&>svg]:text-danger",
    icona: CircleAlert,
    ruolo: "alert",
  },
};

/** Avviso nella pagina: fondo nel tono, bordo sinistro di 4px, icona e parola
 *  (il colore non basta mai da solo). Info e ok sono `status` (annuncio
 *  educato); attenzione ed errore sono `alert` (annuncio immediato). */
export function Alert({ tono, titolo, children, azione, ruolo, className }: AlertProps) {
  const { classi, icona: Icona, ruolo: ruoloDelTono } = toni[tono];
  const ruoloEffettivo = ruolo ?? ruoloDelTono;
  return (
    <div
      role={ruoloEffettivo === "none" ? undefined : ruoloEffettivo}
      className={cn(
        "flex items-start gap-3 rounded-control border border-l-4 px-4 py-3 text-body text-ink",
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
