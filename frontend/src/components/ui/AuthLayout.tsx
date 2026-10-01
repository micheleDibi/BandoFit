import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { cn } from "../../lib/cn";
import { Logo } from "../layout/Logo";

export interface AuthLayoutProps {
  /** Il modulo (titolo, campi, pulsante, link), largo al massimo 380px. */
  children: ReactNode;
  /** Colonna destra su `desk` (la promessa «Fa per me? Quanto vale? Entro
   *  quando?» con due righe di esempio); assente = una colonna sola. */
  laterale?: ReactNode;
  /** Classi del contenitore (la vetrina lo mostra in un riquadro). */
  className?: string;
}

/** Cornice delle pagine di accesso (tavola «Accesso»): a sinistra il logo
 *  allineato a sinistra e il modulo sul foglio, in una colonna da 560px; a
 *  destra il piano con `laterale`, che occupa il resto. Da `lg` due colonne,
 *  sotto una sola (il laterale sparisce). Il logo porta alla landing. */
export function AuthLayout({ children, laterale, className }: AuthLayoutProps) {
  return (
    <div
      className={cn(
        "grid min-h-dvh bg-sheet",
        laterale && "lg:grid-cols-[560px_minmax(0,1fr)]",
        className,
      )}
    >
      <div className="flex min-w-0 flex-col px-6 py-10 sm:px-10 lg:px-20 lg:py-12">
        <Link
          to="/"
          className="self-start rounded-control focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
        >
          <Logo variant="stack" />
        </Link>
        <main className="my-auto w-full max-w-95 py-12">{children}</main>
      </div>
      {laterale && (
        <aside className="hidden min-w-0 border-l border-line bg-desk lg:flex lg:items-center lg:px-18 lg:py-12">
          <div className="w-full">{laterale}</div>
        </aside>
      )}
    </div>
  );
}
