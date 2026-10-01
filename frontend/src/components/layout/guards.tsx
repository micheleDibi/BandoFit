import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";
import { useFunzioni } from "../../hooks/useFunzioni";
import { useMe } from "../../hooks/useMe";
import { hasAreaProgettista } from "../../lib/roles";
import NonDisponibile from "../../pages/NonDisponibile";
import { Spinner } from "../ui/Spinner";

/** Prima della cornice (sessione ancora da leggere): alto quanto la finestra. */
function FullPageSpinner() {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-desk">
      <Spinner size="lg" label="Caricamento" />
    </div>
  );
}

/** Dentro la cornice (ruolo o flag ancora da leggere): un blocco, non una finestra. */
function SpinnerInCornice() {
  return (
    <div className="flex min-h-64 items-center justify-center">
      <Spinner size="lg" label="Caricamento" />
    </div>
  );
}

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { session } = useAuth();
  const location = useLocation();

  if (session === undefined) return <FullPageSpinner />;
  if (session === null) {
    // Dopo il login si torna ESATTAMENTE qui: con la query (filtri, scheda)
    // e l'hash (ancore dei vecchi deep-link).
    return (
      <Navigate
        to="/login"
        state={{ from: location.pathname + location.search + location.hash }}
        replace
      />
    );
  }
  return <>{children}</>;
}

/** Accesso negato = «Questa pagina non è disponibile» dentro la cornice,
 *  per ogni guard: niente redirect che rivelino che cosa esiste. */
export function AdminRoute({ children }: { children: ReactNode }) {
  const { data: me, isPending } = useMe();

  if (isPending) return <SpinnerInCornice />;
  if (me?.profile.role !== "admin") return <NonDisponibile />;
  return <>{children}</>;
}

export function ProgettistaRoute({ children }: { children: ReactNode }) {
  const { data: me, isPending } = useMe();

  if (isPending) return <SpinnerInCornice />;
  if (!hasAreaProgettista(me?.profile.role)) return <NonDisponibile />;
  return <>{children}</>;
}

/** Pagine del modulo partenariati: a modulo spento la pagina «non esiste»,
 *  come le rotte del backend (404). */
export function PartenariatiRoute({ children }: { children: ReactNode }) {
  const { partenariatiAttivo, isPending } = useFunzioni();

  if (isPending) return <SpinnerInCornice />;
  if (!partenariatiAttivo) return <NonDisponibile />;
  return <>{children}</>;
}
