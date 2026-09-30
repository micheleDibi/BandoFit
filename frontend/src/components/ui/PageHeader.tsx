import { ChevronLeft } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { cn } from "../../lib/cn";

export interface Ritorno {
  label: string;
  /** La pagina madre; `location.state.from` come `{ to, label }` ha la precedenza. */
  to: string;
}

/** Solo un `from` completo (`{ to, label }`) sostituisce il ritorno: una stringa
 *  (il `from` di `guards.tsx` verso il login) non c'entra e viene ignorata,
 *  altrimenti il link punterebbe altrove con l'etichetta della pagina madre. */
function ritornoDaState(state: unknown, base: Ritorno): Ritorno {
  const from = (state as { from?: unknown } | null)?.from;
  if (from && typeof from === "object") {
    const { to, label } = from as Partial<Ritorno>;
    if (typeof to === "string" && to && typeof label === "string" && label) return { to, label };
  }
  return base;
}

/** Link di ritorno («‹ Bandi»): usa `location.state.from` se presente, altrimenti `to`. */
export function BackLink({ label, to, className }: Ritorno & { className?: string }) {
  const location = useLocation();
  const dest = ritornoDaState(location.state, { label, to });
  return (
    <Link
      to={dest.to}
      className={cn(
        "inline-flex items-center gap-1 self-start rounded-mark text-small font-medium text-ink-2 hover:text-ink",
        className,
      )}
    >
      <ChevronLeft className="size-4" strokeWidth={1.75} aria-hidden />
      {dest.label}
    </Link>
  );
}

export interface PageHeaderProps {
  /** Uguale alla voce di menu. */
  titolo: ReactNode;
  descrizione?: ReactNode;
  indietro?: Ritorno;
  /** A destra: un solo pulsante pieno per schermata. */
  azioni?: ReactNode;
  /** Riga sopra il titolo: stato e tipologia nella scheda del bando. */
  sopra?: ReactNode;
  /** `bando` = `title-bando`, solo per il titolo del bando nella sua scheda. */
  stileTitolo?: "pagina" | "bando";
  className?: string;
}

/** Intestazione di pagina: ritorno, titolo, una riga di descrizione, azioni a destra. */
export function PageHeader({
  titolo,
  descrizione,
  indietro,
  azioni,
  sopra,
  stileTitolo = "pagina",
  className,
}: PageHeaderProps) {
  return (
    <header className={cn("flex flex-col gap-4", className)}>
      {indietro && <BackLink {...indietro} />}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
        <div className="flex min-w-0 flex-col gap-1">
          {sopra && <div className="flex flex-wrap items-center gap-x-4 gap-y-1">{sopra}</div>}
          <h1
            className={cn(
              "text-ink",
              stileTitolo === "bando" ? "text-title-bando text-balance" : "text-title-page",
            )}
          >
            {titolo}
          </h1>
          {descrizione && <p className="text-body text-ink-2">{descrizione}</p>}
        </div>
        {azioni && <div className="flex shrink-0 flex-wrap items-center gap-2">{azioni}</div>}
      </div>
    </header>
  );
}
