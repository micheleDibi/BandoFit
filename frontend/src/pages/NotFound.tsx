import { Logo } from "../components/layout/Logo";
import { LinkButton } from "../components/ui/Button";

/** Pagina non trovata FUORI dalla cornice (URL pubblici): dentro /app ci
 *  pensa `NonDisponibile`. */
export default function NotFound() {
  return (
    <div className="flex min-h-dvh items-center bg-sheet px-4 py-12 sm:px-10">
      <div className="mx-auto flex w-full max-w-[520px] flex-col items-start gap-2">
        <div className="mb-6">
          <Logo variant="stack" />
        </div>
        <h1 className="text-title-page text-ink">Pagina non trovata</h1>
        <p className="text-body text-ink-2">
          La pagina che cerchi non esiste o è stata spostata.
        </p>
        <LinkButton to="/" variant="secondary" className="mt-2">
          Torna alla Home
        </LinkButton>
      </div>
    </div>
  );
}
