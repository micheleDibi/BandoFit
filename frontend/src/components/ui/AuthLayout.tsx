import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { cn } from "../../lib/cn";
import { Logo } from "../layout/Logo";

export interface AuthLayoutProps {
  /** Il modulo (titolo, campi, pulsante, link), largo al massimo 380px. */
  children: ReactNode;
  /** Colonna destra sulla fascia navy (`bg-banda`, testo bianco): la promessa
   *  «Fa per me? Quanto vale? Entro quando?» con due righe d'esempio come card
   *  bianche; assente = una colonna sola. */
  laterale?: ReactNode;
  /** Classi del contenitore (la vetrina lo mostra in un riquadro). */
  className?: string;
}

/** Cornice delle pagine di accesso (tavola «Accesso»): a sinistra il logo
 *  allineato a sinistra e il modulo sul foglio bianco, in una colonna da 560px;
 *  a destra la fascia navy con `laterale`, che occupa il resto. Da `lg` due
 *  colonne, sotto una sola (il laterale sparisce). Il logo porta alla landing. */
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
        <main className="my-auto w-full max-w-95 py-12 motion-safe:animate-entrata">
          {children}
        </main>
      </div>
      {laterale && (
        <aside className="relative hidden min-w-0 overflow-hidden bg-banda text-white lg:flex lg:items-center lg:px-18 lg:py-12">
          {/* Due cerchi a filo, solo decorazione: profondità senza un secondo gradiente. */}
          <span
            aria-hidden
            className="pointer-events-none absolute -top-40 -right-40 size-[30rem] rounded-full border border-white/10"
          />
          <span
            aria-hidden
            className="pointer-events-none absolute -bottom-48 -left-32 size-[26rem] rounded-full border border-white/10"
          />
          <div className="relative mx-auto w-full max-w-2xl">{laterale}</div>
        </aside>
      )}
    </div>
  );
}
