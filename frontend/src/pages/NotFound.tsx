import { AuthLayout } from "../components/ui/AuthLayout";
import { LinkButton } from "../components/ui/Button";

/** Pagina non trovata FUORI dalla cornice (URL pubblici): dentro /app ci
 *  pensa `NonDisponibile`. Stessa cornice delle pagine di accesso, senza
 *  pannello laterale. */
export default function NotFound() {
  return (
    <AuthLayout>
      <div className="flex flex-col items-start gap-2">
        <h1 className="text-title-page text-ink">Pagina non trovata</h1>
        <p className="text-body text-ink-2">La pagina che cerchi non esiste o è stata spostata.</p>
        <LinkButton to="/" variant="secondary" className="mt-4">
          Torna alla Home
        </LinkButton>
      </div>
    </AuthLayout>
  );
}
