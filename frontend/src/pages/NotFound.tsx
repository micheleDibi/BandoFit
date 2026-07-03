import { Link } from "react-router-dom";
import { Logo } from "../components/layout/Logo";
import { Button } from "../components/ui/Button";

export default function NotFound() {
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center bg-surface px-4 text-center">
      <Logo variant="vertical" />
      <p className="mt-8 font-display text-6xl font-bold text-brand-200">404</p>
      <h1 className="mt-3 font-display text-xl font-semibold text-slate-900">
        Pagina non trovata
      </h1>
      <p className="mt-2 max-w-sm text-sm text-slate-500">
        La pagina che cerchi non esiste o è stata spostata.
      </p>
      <Link to="/" className="mt-6">
        <Button>Torna alla home</Button>
      </Link>
    </div>
  );
}
